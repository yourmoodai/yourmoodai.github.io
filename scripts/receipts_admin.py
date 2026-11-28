"""Explicit operator preflight, public key export, and registry activation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.request

from legal import ROOT, encoded, digest, load, verify_public_receipt, timestamp

ACCOUNT = '463509296941'
REGION = 'us-east-1'


def session():
    if os.environ.get('AWS_PROFILE') != 'awill' or os.environ.get('AWS_REGION') != REGION:
        raise ValueError('set_explicit_awill_profile_and_us_east_1_region')
    import boto3
    value = boto3.Session(profile_name='awill', region_name=REGION)
    identity = value.client('sts').get_caller_identity()
    if identity['Account'] != ACCOUNT: raise ValueError('aws_account_mismatch')
    return value


def stack_outputs(aws, name):
    stack = aws.client('cloudformation').describe_stacks(StackName=name)['Stacks'][0]
    if stack['StackStatus'] not in ('CREATE_COMPLETE', 'UPDATE_COMPLETE'):
        raise ValueError('stack_not_ready')
    return {item['OutputKey']: item['OutputValue'] for item in stack['Outputs']}


def public_key(aws, key_id):
    from cryptography.hazmat.primitives import serialization
    result = aws.client('kms').get_public_key(KeyId=key_id)
    if result['KeyUsage'] != 'SIGN_VERIFY' or 'RSASSA_PSS_SHA_256' not in result['SigningAlgorithms']:
        raise ValueError('unexpected_signing_key')
    key = serialization.load_der_public_key(result['PublicKey'])
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def inspect(aws, output, sender):
    s3, ses = aws.client('s3'), aws.client('sesv2')
    blocks = s3.get_public_access_block(Bucket=output['ReceiptsBucket'])['PublicAccessBlockConfiguration']
    if not all(blocks.values()) or s3.get_bucket_versioning(Bucket=output['ReceiptsBucket']).get('Status') != 'Enabled':
        raise ValueError('private_versioned_storage_required')
    identity = ses.get_email_identity(EmailIdentity=sender)
    if not identity.get('VerifiedForSendingStatus'): raise ValueError('sender_not_verified')
    if not ses.get_account().get('ProductionAccessEnabled'): raise ValueError('ses_production_access_required')
    public_key(aws, output['PublicSigningKeyId'])
    return {'schema': 1, 'status': 'infrastructure_preflight_passed', 'account': ACCOUNT,
            'region': REGION, 'api': output['ApiUrl'], 'scope': 'configuration_only',
            'live_acceptance': 'not_verified'}


def activate(aws, output, manifest, receipt_path, approve_digest):
    expected = digest(encoded(manifest))
    if approve_digest != expected: raise ValueError('explicit_manifest_approval_required')
    receipt = verify_public_receipt(receipt_path)
    if receipt['manifest_sha256'] != expected or receipt['source_commit'] != manifest['source_commit']:
        raise ValueError('publication_binding_failed')
    if timestamp(receipt['first_observed_at']) > timestamp(manifest['effective_at']):
        raise ValueError('publication_deadline_missed')
    with urllib.request.urlopen('https://yourmood.ai/legal/manifest.json', timeout=20) as response:
        if response.url != 'https://yourmood.ai/legal/manifest.json' or response.read(1024 * 1024) != encoded(manifest):
            raise ValueError('current_public_manifest_mismatch')
    keys = load(ROOT / 'legal/keys/index.json')
    key_id = output['PublicSigningKeyId']
    choices = [key for key in keys['keys'] if key['key_id'] == key_id]
    if len(choices) != 1: raise ValueError('published_signing_key_required')
    key = choices[0]; key_path = ROOT / 'legal/keys' / key['file']
    key_data = public_key(aws, key_id)
    if digest(key_data) != key['sha256'] or key_path.read_bytes() != key_data:
        raise ValueError('public_key_mismatch')
    if manifest['assets'].get('/legal/keys/' + key['file']) != digest(key_data):
        raise ValueError('public_key_not_bound_to_release')
    config_path = ROOT / 'public/legal/receipt-config.json'
    if not config_path.is_file() or digest(config_path.read_bytes()) != manifest['assets']['/legal/receipt-config.json']:
        raise ValueError('exact_release_config_required')
    if load(config_path)['api'] != output['ApiUrl']:
        raise ValueError('receipt_api_mismatch')
    registry = {k: manifest[k] for k in ['schema', 'release_id', 'effective_at', 'documents']}
    registry |= {'publication_status': 'publication_observed', 'manifest_sha256': expected,
                 'publication_receipt_sha256': digest(encoded(receipt))}
    aws.client('s3').put_object(Bucket=output['ReceiptsBucket'], Key='registry/active.json',
                               Body=encoded(registry), ContentType='application/json')
    return {'status': 'receipt_registry_activated', 'manifest_sha256': expected,
            'live_acceptance': 'requires_end_to_end_verification'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'export-key', 'activate-registry'])
    parser.add_argument('--stack', required=True); parser.add_argument('--sender')
    parser.add_argument('--manifest', type=Path); parser.add_argument('--publication-receipt', type=Path)
    parser.add_argument('--approve-manifest'); args = parser.parse_args()
    aws = session(); output = stack_outputs(aws, args.stack)
    if args.command == 'inspect':
        if not args.sender: raise ValueError('verified_sender_required')
        result = inspect(aws, output, args.sender)
    elif args.command == 'export-key':
        data = public_key(aws, output['PublicSigningKeyId'])
        file = 'receipt-' + digest(data)[:16] + '.pem'
        directory = ROOT / 'legal/keys'; directory.mkdir(parents=True, exist_ok=True)
        catalog = load(directory / 'index.json')
        entry = {'key_id': output['PublicSigningKeyId'], 'file': file, 'sha256': digest(data),
                 'algorithm': 'RSASSA_PSS_SHA_256'}
        if any(key['key_id'] == entry['key_id'] and key != entry for key in catalog['keys']):
            raise ValueError('existing_key_changed')
        if entry not in catalog['keys']: catalog['keys'].append(entry)
        (directory / file).write_bytes(data); (directory / 'index.json').write_bytes(encoded(catalog))
        result = {'status': 'public_key_exported_for_review', 'file': file}
    else:
        if not args.manifest or not args.publication_receipt or not args.approve_manifest:
            raise ValueError('release_evidence_and_explicit_approval_required')
        result = activate(aws, output, load(args.manifest), args.publication_receipt, args.approve_manifest)
    print(json.dumps(result))


if __name__ == '__main__': main()
