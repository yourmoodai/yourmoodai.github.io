"""AWS adapters. Receipt bodies, addresses, capabilities and tokens are never logged."""
import base64
from decimal import Decimal
import hashlib
import json
import os
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError
from service import ReceiptService, Rejected, MAX_BYTES, encode


def plain(value):
    if isinstance(value, Decimal): return int(value)
    if isinstance(value, dict): return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list): return [plain(v) for v in value]
    return value


class AwsBackend:
    def __init__(self):
        self.table = boto3.resource('dynamodb').Table(os.environ['STATE_TABLE'])
        self.dynamo = boto3.client('dynamodb')
        self.s3 = boto3.client('s3'); self.ses = boto3.client('sesv2'); self.kms = boto3.client('kms')
        self.bucket = os.environ['RECEIPTS_BUCKET']

    def registry(self):
        value = self.s3.get_object(Bucket=self.bucket, Key='registry/active.json')['Body'].read(128 * 1024 + 1) # 128 KiB registry limit
        if len(value) > 128 * 1024: raise Rejected('agreements_unavailable', 503)
        return json.loads(value)

    def get(self, pk):
        return plain(self.table.get_item(Key={'pk': pk}, ConsistentRead=True).get('Item'))

    def create(self, row):
        try:
            self.table.put_item(Item=row, ConditionExpression='attribute_not_exists(pk)')
            return True
        except ClientError as error:
            if error.response['Error']['Code'] == 'ConditionalCheckFailedException': return False
            raise

    def replace(self, row, version):
        value = row | {'version': version + 1}
        try:
            self.table.put_item(Item=value, ConditionExpression='#v = :v',
                                ExpressionAttributeNames={'#v': 'version'}, ExpressionAttributeValues={':v': version})
            return True
        except ClientError as error:
            if error.response['Error']['Code'] == 'ConditionalCheckFailedException': return False
            raise

    def commit_acceptance(self, record, moment, upload_version):
        from boto3.dynamodb.types import TypeSerializer
        serializer = TypeSerializer()
        serialize = lambda value: {k: serializer.serialize(v) for k, v in value.items()}
        value = record | {'state': 'committing', 'committed_at': moment,
                          'upload_version': upload_version, 'version': record['version'] + 1}
        try:
            self.dynamo.transact_write_items(TransactItems=[
                {'ConditionCheck': {'TableName': self.table.name,
                    'Key': serialize({'pk': 'subject:' + record['subject']}),
                    'ConditionExpression': 'attribute_not_exists(pk) OR closed_at < :created',
                    'ExpressionAttributeValues': {':created': serializer.serialize(record['created_at'])}}},
                {'Put': {'TableName': self.table.name, 'Item': serialize(value),
                    'ConditionExpression': '#v=:v AND #s=:pending',
                    'ExpressionAttributeNames': {'#v': 'version', '#s': 'state'},
                    'ExpressionAttributeValues': {':v': serializer.serialize(record['version']),
                                                  ':pending': serializer.serialize('pending')}}}])
            return value
        except ClientError as error:
            if error.response['Error']['Code'] == 'TransactionCanceledException':
                latest = self.get(record['pk'])
                return latest if latest and latest['state'] == 'committing' else None
            raise

    def delete(self, pk, version):
        try:
            self.table.delete_item(Key={'pk': pk}, ConditionExpression='#v=:v',
                ExpressionAttributeNames={'#v': 'version'}, ExpressionAttributeValues={':v': version})
            return True
        except ClientError as error:
            if error.response['Error']['Code'] == 'ConditionalCheckFailedException': return False
            raise

    def rate_limit(self, key, moment, limit):
        try:
            self.table.update_item(Key={'pk': f'rate:{moment // 3600}:{key}'},
                UpdateExpression='SET #k=:k, #ttl=:ttl ADD #n :one',
                ConditionExpression='attribute_not_exists(#n) OR #n < :limit',
                ExpressionAttributeNames={'#n': 'attempts', '#k': 'kind', '#ttl': 'ttl'},
                ExpressionAttributeValues={':one': 1, ':limit': limit, ':ttl': moment + 7200, ':k': 'rate'})
        except ClientError as error:
            if error.response['Error']['Code'] == 'ConditionalCheckFailedException': raise Rejected('rate_limited', 429)
            raise

    def for_subject(self, subject):
        values = []
        args = {'IndexName': 'by_subject', 'KeyConditionExpression': '#s=:s',
                'ExpressionAttributeNames': {'#s': 'subject'}, 'ExpressionAttributeValues': {':s': subject}}
        while True:
            result = self.table.query(**args); values.extend(plain(result['Items']))
            if 'LastEvaluatedKey' not in result: return values
            args['ExclusiveStartKey'] = result['LastEvaluatedKey']

    def scan_kind(self, kind):
        args = {'FilterExpression': '#k=:k', 'ExpressionAttributeNames': {'#k': 'kind'},
                'ExpressionAttributeValues': {':k': kind}}
        while True:
            result = self.table.scan(**args)
            yield from plain(result['Items'])
            if 'LastEvaluatedKey' not in result: return
            args['ExclusiveStartKey'] = result['LastEvaluatedKey']

    def subjects(self): return self.scan_kind('subject')

    def receipts(self): return self.scan_kind('receipt')

    def send_email(self, email, identifier, token, purpose, document):
        link = f'https://yourmood.ai/legal/receipts/#{identifier}.{quote(token, safe="")}'
        detail = ('Agreement: ' + document['title'] + '\nVersion: ' + document['version'] +
                  '\nSHA-256: ' + document['sha256'] + '\n' + document['url'] + '\n') if document else ''
        body = (f'You requested {purpose} verification for Your Mood receipts.\n{detail}\n'
                f'{link}\n\nExpires in 15 minutes. Opening this link does not accept an agreement. '
                'Return to the page and make an explicit choice. Ignore this email if you did not request it.')
        self.ses.send_email(FromEmailAddress=os.environ['VERIFICATION_FROM'], Destination={'ToAddresses': [email]},
                            Content={'Simple': {'Subject': {'Data': 'Verify your Your Mood receipt request'},
                                                'Body': {'Text': {'Data': body}}}})

    def upload_url(self, identifier, checksum, length, seconds):
        return self.s3.generate_presigned_url('put_object', Params={'Bucket': self.bucket,
            'Key': 'pending/' + identifier, 'ContentType': 'application/json', 'ContentLength': length,
            'ChecksumSHA256': checksum, 'IfNoneMatch': '*'}, ExpiresIn=seconds)

    def pending(self, identifier):
        try:
            result = self.s3.get_object(Bucket=self.bucket, Key='pending/' + identifier)
            value = result['Body'].read(MAX_BYTES + 1)
            if len(value) > MAX_BYTES: raise Rejected('upload_too_large', 413)
            return value, result.get('VersionId')
        except ClientError as error:
            if error.response['Error']['Code'] in ('NoSuchKey', '404'): raise Rejected('upload_missing', 409)
            raise

    def sign(self, data):
        result = self.kms.sign(KeyId=os.environ['SIGNING_KEY'], Message=hashlib.sha256(data).digest(),
                               MessageType='DIGEST', SigningAlgorithm='RSASSA_PSS_SHA_256')
        return result['Signature'], result['KeyId']

    def store_final(self, identifier, value):
        try:
            self.s3.put_object(Bucket=self.bucket, Key='receipts/' + identifier, Body=encode(value),
                               ContentType='application/json', IfNoneMatch='*')
            return value
        except ClientError as error:
            if error.response['Error']['Code'] in ('PreconditionFailed', 'ConditionalRequestConflict'):
                return self.final_receipt(identifier)
            raise

    def revoke_upload(self, identifier):
        try:
            self.s3.put_object(Bucket=self.bucket, Key='pending/' + identifier,
                               Body=b'{}', ContentType='application/json', IfNoneMatch='*')
        except ClientError as error:
            if error.response['Error']['Code'] not in ('PreconditionFailed', 'ConditionalRequestConflict'): raise

    def final_receipt(self, identifier):
        result = self.s3.get_object(Bucket=self.bucket, Key='receipts/' + identifier)
        value = result['Body'].read(2 * MAX_BYTES + 1) # 32 KiB signed envelope limit
        if len(value) > 2 * MAX_BYTES: raise Rejected('receipt_unavailable', 503)
        return json.loads(value)

    def existing_final(self, identifier):
        try: return self.final_receipt(identifier)
        except ClientError as error:
            if error.response['Error']['Code'] in ('NoSuchKey', '404'): return None
            raise

    def download_url(self, identifier, seconds):
        return self.s3.generate_presigned_url('get_object', Params={'Bucket': self.bucket,
            'Key': 'receipts/' + identifier, 'ResponseContentType': 'application/json',
            'ResponseContentDisposition': 'attachment; filename="acceptance-receipt.json"'}, ExpiresIn=seconds)

    def delete_receipt(self, identifier):
        # Delete every version, not just add a delete marker over retained content.
        key = 'receipts/' + identifier
        paginator = self.s3.get_paginator('list_object_versions')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=key):
            objects = [{'Key': o['Key'], 'VersionId': o['VersionId']}
                       for o in page.get('Versions', []) + page.get('DeleteMarkers', []) if o['Key'] == key]
            if objects:
                result = self.s3.delete_objects(Bucket=self.bucket, Delete={'Objects': objects, 'Quiet': True})
                if result.get('Errors'): raise Rejected('cleanup_incomplete', 503)


def handler(event, context):
    response_headers = {'Content-Type': 'application/json', 'Cache-Control': 'no-store'}
    try:
        backend = AwsBackend(); service = ReceiptService(backend)
        if event.get('source') == 'legal.receipts.cleanup':
            # Scheduled failures must remain retryable by EventBridge.
            try: return service.cleanup()
            except Exception:
                raise RuntimeError('receipt_cleanup_failed') from None
        if event.get('requestContext', {}).get('http', {}).get('method') != 'POST': raise Rejected('method_not_allowed', 405)
        raw = event.get('body') or ''
        if len(raw) > 2 * MAX_BYTES: raise Rejected('request_too_large', 413)
        if event.get('isBase64Encoded'): raw = base64.b64decode(raw, validate=True).decode()
        if len(raw.encode()) > MAX_BYTES: raise Rejected('request_too_large', 413)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise Rejected('duplicate_key')
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(Rejected('invalid_request')))
        path = event.get('rawPath', '')
        ip = event['requestContext']['http'].get('sourceIp', 'unknown')
        result = service.route(path, value, ip)
        return {'statusCode': 200, 'headers': response_headers, 'body': encode(result).decode()}
    except Rejected as error:
        return {'statusCode': error.status, 'headers': response_headers, 'body': json.dumps({'code': error.code})}
    except (ValueError, KeyError, TypeError):
        return {'statusCode': 400, 'headers': response_headers, 'body': '{"code":"invalid_request"}'}
    except Exception:
        # Provider messages can contain addresses, request bodies or capabilities.
        print(json.dumps({'code': 'receipt_service_unavailable'}))
        if event.get('source') == 'legal.receipts.cleanup':
            raise RuntimeError('receipt_cleanup_failed') from None
        return {'statusCode': 503, 'headers': response_headers, 'body': '{"code":"service_unavailable"}'}
