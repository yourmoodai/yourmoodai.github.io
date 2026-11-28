import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import boto3
from botocore.config import Config
from botocore.stub import Stubber

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'infra/receipts'))
sys.path.insert(0, str(ROOT / 'scripts'))
import app
import receipts_admin


class AwsAdapters(unittest.TestCase):
    def setUp(self):
        # Explicit synthetic credentials keep these tests entirely offline.
        environment = mock.patch.dict(os.environ, {'AWS_CONFIG_FILE': os.devnull,
                                                   'AWS_SHARED_CREDENTIALS_FILE': os.devnull})
        environment.start(); self.addCleanup(environment.stop)
        os.environ.pop('AWS_PROFILE', None); os.environ.pop('AWS_DEFAULT_PROFILE', None)
        self.aws = boto3.Session(aws_access_key_id='synthetic', aws_secret_access_key='synthetic', region_name='us-east-1')
        self.backend = object.__new__(app.AwsBackend)
        self.backend.bucket = 'synthetic-private-receipts'
        self.backend.s3 = self.aws.client('s3', config=Config(signature_version='s3v4'))
        self.backend.dynamo = self.aws.client('dynamodb')
        self.backend.kms = self.aws.client('kms')
        self.backend.table = mock.Mock(name='table'); self.backend.table.name = 'synthetic-state'

    def test_acceptance_transaction_binds_subject_cutoff_and_pending_version(self):
        row = {'pk': 'receipt:test', 'kind': 'receipt', 'subject': 'email-digest',
               'state': 'pending', 'version': 2, 'created_at': 100}
        with Stubber(self.backend.dynamo) as stub:
            # Botocore validates this against the real low-level API schema.
            stub.add_response('transact_write_items', {}, expected_params={
                'TransactItems': [
                    {'ConditionCheck': {'TableName': 'synthetic-state',
                        'Key': {'pk': {'S': 'subject:email-digest'}},
                        'ConditionExpression': 'attribute_not_exists(pk) OR closed_at < :created',
                        'ExpressionAttributeValues': {':created': {'N': '100'}}}},
                    {'Put': {'TableName': 'synthetic-state',
                        'Item': {'pk': {'S': 'receipt:test'}, 'kind': {'S': 'receipt'},
                            'subject': {'S': 'email-digest'}, 'state': {'S': 'committing'},
                            'version': {'N': '3'}, 'created_at': {'N': '100'},
                            'committed_at': {'N': '110'}, 'upload_version': {'S': 'object-version'}},
                        'ConditionExpression': '#v=:v AND #s=:pending',
                        'ExpressionAttributeNames': {'#v': 'version', '#s': 'state'},
                        'ExpressionAttributeValues': {':v': {'N': '2'}, ':pending': {'S': 'pending'}}}}]})
            result = self.backend.commit_acceptance(row, 110, 'object-version')
            self.assertEqual(result['state'], 'committing')
            stub.assert_no_pending_responses()

    def test_presigned_upload_binds_checksum_and_conditional_write(self):
        checksum = base64.b64encode(hashlib.sha256(b'synthetic').digest()).decode()
        url = self.backend.upload_url('identifier', checksum, 9, 300)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['X-Amz-Expires'], ['300'])
        headers = query['X-Amz-SignedHeaders'][0].split(';')
        for name in ['content-length', 'content-type', 'if-none-match', 'x-amz-checksum-sha256']:
            self.assertIn(name, headers)

    def test_kms_signs_digest_with_the_verifier_algorithm(self):
        arn = 'arn:aws:kms:us-east-1:463509296941:key/synthetic'
        with Stubber(self.backend.kms) as stub, mock.patch.dict(os.environ, {'SIGNING_KEY': arn}):
            stub.add_response('sign', {'Signature': b'synthetic-signature', 'KeyId': arn,
                'SigningAlgorithm': 'RSASSA_PSS_SHA_256'}, {'KeyId': arn,
                'Message': hashlib.sha256(b'exact-payload').digest(), 'MessageType': 'DIGEST',
                'SigningAlgorithm': 'RSASSA_PSS_SHA_256'})
            self.assertEqual(self.backend.sign(b'exact-payload'), (b'synthetic-signature', arn))

    def test_cleanup_removes_versions_and_does_not_hide_access_denial(self):
        with Stubber(self.backend.s3) as stub:
            stub.add_response('list_object_versions', {'IsTruncated': False,
                'Versions': [{'Key': 'receipts/id', 'VersionId': 'v1'},
                             {'Key': 'receipts/id-other', 'VersionId': 'v2'}],
                'DeleteMarkers': [{'Key': 'receipts/id', 'VersionId': 'marker'}]},
                {'Bucket': self.backend.bucket, 'Prefix': 'receipts/id'})
            stub.add_response('delete_objects', {}, {'Bucket': self.backend.bucket,
                'Delete': {'Objects': [{'Key': 'receipts/id', 'VersionId': 'v1'},
                                      {'Key': 'receipts/id', 'VersionId': 'marker'}], 'Quiet': True}})
            self.backend.delete_receipt('id')
            stub.add_client_error('get_object', service_error_code='AccessDenied', http_status_code=403)
            with self.assertRaises(Exception): self.backend.existing_final('id')
            stub.add_client_error('get_object', service_error_code='NoSuchKey', http_status_code=404)
            self.assertIsNone(self.backend.existing_final('id'))

    def test_http_errors_never_echo_provider_details_or_request_content(self):
        event = {'requestContext': {'http': {'method': 'POST'}}, 'rawPath': '/challenges',
                 'body': '{"email":"private@example.test","email":"duplicate@example.test"}'}
        with mock.patch.object(app, 'AwsBackend', return_value=mock.Mock()):
            response = app.handler(event, None)
            self.assertEqual(response['statusCode'], 400)
            self.assertNotIn('@', response['body'])
        captured = io.StringIO()
        with mock.patch.object(app, 'AwsBackend', side_effect=RuntimeError('private@example.test token')):
            with redirect_stdout(captured): response = app.handler(event, None)
        self.assertEqual(response['statusCode'], 503)
        self.assertNotIn('private', captured.getvalue() + response['body'])
        with mock.patch.object(app, 'AwsBackend', side_effect=RuntimeError('private@example.test')):
            with redirect_stdout(captured), self.assertRaisesRegex(RuntimeError, '^receipt_cleanup_failed$'):
                app.handler({'source': 'legal.receipts.cleanup'}, None)

    def test_operator_profile_and_account_boundaries_have_no_fallback(self):
        with mock.patch.dict(os.environ, {'AWS_PROFILE': 'other', 'AWS_REGION': 'us-east-1'}):
            with mock.patch.object(boto3, 'Session') as factory:
                with self.assertRaisesRegex(ValueError, 'set_explicit'): receipts_admin.session()
                factory.assert_not_called()
        with mock.patch.dict(os.environ, {'AWS_PROFILE': 'awill', 'AWS_REGION': 'us-east-1'}):
            with mock.patch.object(boto3, 'Session') as factory:
                factory.return_value.client.return_value.get_caller_identity.return_value = {'Account': '000000000000'}
                with self.assertRaisesRegex(ValueError, 'aws_account_mismatch'): receipts_admin.session()
                factory.assert_called_once_with(profile_name='awill', region_name='us-east-1')


if __name__ == '__main__': unittest.main()
