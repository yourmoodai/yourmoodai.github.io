"""Purpose-bound email verification and acceptance receipts; no prompt processing."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

MAX_BYTES = 16 * 1024  # 16 KiB, acceptance payload and request limit
CHALLENGE_SECONDS = 15 * 60
UPLOAD_SECONDS = 5 * 60


def encode(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def epoch(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        raise ValueError('invalid_timestamp')
    return int(datetime.fromisoformat(value[:-1] + '+00:00').timestamp())


def retention_end(closed_at):
    value = datetime.fromtimestamp(closed_at, timezone.utc)
    try: until = value.replace(year=value.year + 3)
    except ValueError: until = value.replace(year=value.year + 3, day=28)
    return int(until.timestamp())


class Rejected(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


def require(condition, code='invalid_request', status=400):
    if not condition:
        raise Rejected(code, status)


def fields(value, required, optional=()):
    require(isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(optional))


def email_address(value):
    require(isinstance(value, str) and len(value) <= 254)
    require(re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value) is not None)
    # Preserve local-part spelling; do not merge providers' aliases or mailboxes.
    local, domain = value.rsplit('@', 1)
    return local + '@' + domain.lower()


class ReceiptService:
    def __init__(self, backend, clock=time.time):
        self.backend, self.clock = backend, clock

    def current_document(self, document_id):
        registry = self.backend.registry()
        require(registry.get('schema') == 1 and registry.get('publication_status') == 'publication_observed', 'agreements_unavailable', 503)
        require(epoch(registry['effective_at']) <= int(self.clock()), 'agreement_not_effective', 409)
        choices = [d for d in registry['documents'] if d['id'] == document_id]
        require(len(choices) == 1, 'unknown_agreement')
        doc = choices[0]
        parsed = urlsplit(doc['url'])
        require(parsed.scheme == 'https' and parsed.netloc == 'yourmood.ai'
                and parsed.path.startswith('/legal/versions/') and not parsed.query and not parsed.fragment,
                'agreements_unavailable', 503)
        require(re.fullmatch(r'[a-f0-9]{64}', doc['sha256']) is not None, 'agreements_unavailable', 503)
        return {k: doc[k] for k in ['id', 'title', 'version', 'sha256', 'url']} | {'release_id': registry['release_id']}

    def challenge(self, body, source_ip):
        fields(body, ['email', 'purpose'], ['document_id'])
        require(body['purpose'] in ('accept', 'access', 'close'))
        email = email_address(body['email'])
        moment = int(self.clock()); subject = sha(email.encode())
        self.backend.rate_limit('email:' + subject, moment, 3)
        self.backend.rate_limit('network:' + sha(source_ip.encode()), moment, 10)
        doc = self.current_document(body.get('document_id')) if body['purpose'] == 'accept' else None
        require(body['purpose'] == 'accept' or 'document_id' not in body)
        identifier, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        row = {'pk': 'challenge:' + identifier, 'kind': 'challenge', 'state': 'issued', 'version': 1,
               'subject': subject, 'email': email, 'purpose': body['purpose'], 'document': doc,
               'token_sha256': sha(token.encode()), 'created_at': moment,
               'expires_at': moment + CHALLENGE_SECONDS, 'ttl': moment + 86400}
        require(self.backend.create(row), 'challenge_unavailable', 503)
        self.backend.send_email(email, identifier, token, body['purpose'], doc)
        # Never return a verification token through the unauthenticated API.
        return {'status': 'verification_requested', 'expires_in': CHALLENGE_SECONDS}

    def proof(self, body, purpose):
        fields(body, ['challenge', 'token'], ['confirm'])
        require(isinstance(body['challenge'], str) and re.fullmatch(r'[a-f0-9]{32}', body['challenge']) is not None)
        require(isinstance(body['token'], str) and 20 <= len(body['token']) <= 128)
        row = self.backend.get('challenge:' + body['challenge'])
        require(row is not None, 'verification_failed', 403)
        require(hmac.compare_digest(row['token_sha256'], sha(body['token'].encode())), 'verification_failed', 403)
        require(purpose is None or row['purpose'] == purpose, 'wrong_verification_purpose', 403)
        require(row['expires_at'] > int(self.clock()), 'verification_expired', 403)
        return row

    def verification(self, body):
        fields(body, ['challenge', 'token'])
        row = self.proof(body, None)
        return {'purpose': row['purpose'], 'document': row['document'], 'status': row['state']}

    def closed_at(self, subject):
        return (self.backend.get('subject:' + subject) or {}).get('closed_at', 0)

    def uploads(self, body):
        row = self.proof(body, 'accept')
        require(body.get('confirm') is True, 'explicit_acceptance_required')
        doc = self.current_document(row['document']['id'])
        require(doc == row['document'], 'agreement_changed', 409)
        require(row['state'] in ('issued', 'pending'), 'verification_used', 409)
        require(row['created_at'] > self.closed_at(row['subject']), 'verification_revoked', 403)
        identifier = row['pk'].split(':', 1)[1]
        pk = 'receipt:' + identifier
        moment = int(self.clock())
        payload = {'schema': 1, 'receipt_id': identifier, 'action': 'accept', 'email': row['email'],
                   'agreement': doc, 'accepted_at': utc(moment),
                   'verification': {'method': 'email_link', 'verified_at': utc(moment),
                                    'identity_assurance': 'mailbox_control_only'}}
        record = {'pk': pk, 'kind': 'receipt', 'state': 'pending', 'version': 1,
                  'subject': row['subject'], 'created_at': row['created_at'],
                  'payload': payload, 'expires_at': moment + 86400}
        self.backend.create(record)
        record = self.backend.get(pk)
        require(record is not None and record['state'] == 'pending' and record['subject'] == row['subject'], 'receipt_unavailable', 409)
        if row['state'] == 'issued':
            require(self.backend.replace(row | {'state': 'pending'}, row['version']), 'concurrent_request', 409)
        data = encode(record['payload']); require(len(data) <= MAX_BYTES)
        checksum = base64.b64encode(hashlib.sha256(data).digest()).decode()
        return {'receipt_id': identifier, 'payload': data.decode(), 'sha256': sha(data),
                'upload_url': self.backend.upload_url(identifier, checksum, len(data), UPLOAD_SECONDS),
                'headers': {'Content-Type': 'application/json', 'x-amz-checksum-sha256': checksum, 'If-None-Match': '*'},
                'expires_in': UPLOAD_SECONDS}

    def finalize(self, body):
        row = self.proof(body, 'accept')
        require('confirm' not in body)
        identifier = row['pk'].split(':', 1)[1]
        record = self.backend.get('receipt:' + identifier)
        require(record is not None and record['subject'] == row['subject'], 'receipt_unavailable', 409)
        if record['state'] in ('active', 'closed'):
            return self.backend.final_receipt(identifier)
        if record['state'] == 'pending':
            require(row['state'] == 'pending', 'verification_used', 409)
            require(self.current_document(row['document']['id']) == row['document'], 'agreement_changed', 409)
            uploaded, version = self.backend.pending(identifier)
            require(uploaded == encode(record['payload']) and version, 'upload_mismatch', 409)
            record = self.backend.commit_acceptance(record, int(self.clock()), version)
            require(record is not None, 'verification_revoked', 409)
        require(record['state'] == 'committing', 'verification_used', 409)
        envelope = self.complete_committed(record)
        challenge = self.backend.get(row['pk'])
        if challenge['state'] == 'pending':
            self.backend.replace(challenge | {'state': 'completed'}, challenge['version'])
        return envelope

    def complete_committed(self, record):
        identifier = record['pk'].split(':', 1)[1]
        envelope = self.backend.existing_final(identifier)
        if envelope is None:
            data = encode(record['payload'] | {'acceptance_committed_at': utc(record['committed_at']),
                                              'upload_version': record['upload_version']})
            signature, key_id = self.backend.sign(data)
            envelope = self.backend.store_final(identifier, {'schema': 1,
                'payload': base64.b64encode(data).decode(), 'signature': base64.b64encode(signature).decode(),
                'key_id': key_id, 'algorithm': 'RSASSA_PSS_SHA_256'})
        updated = {k: v for k, v in record.items() if k != 'expires_at'}
        closed = self.closed_at(record['subject'])
        if closed and record['created_at'] <= closed:
            updated |= {'state': 'closed', 'closed_at': closed, 'expires_at': retention_end(closed)}
        else:
            updated['state'] = 'active'
        if not self.backend.replace(updated, record['version']):
            latest = self.backend.get(record['pk'])
            require(latest is not None and latest['state'] in ('active', 'closed'), 'concurrent_request', 409)
        return envelope

    def access(self, body):
        row = self.proof(body, 'access')
        require(row['state'] == 'issued', 'verification_used', 409)
        require('confirm' not in body)
        require(self.backend.replace(row | {'state': 'completed'}, row['version']), 'verification_used', 409)
        receipts = []
        moment = int(self.clock()); closed = self.closed_at(row['subject'])
        for record in self.backend.for_subject(row['subject']):
            if record['kind'] != 'receipt' or record['state'] not in ('active', 'closed'): continue
            if closed and record['created_at'] <= closed and retention_end(closed) <= moment: continue
            if record.get('expires_at', moment + 1) <= moment: continue
            identifier = record['pk'].split(':', 1)[1]
            receipts.append({'receipt_id': identifier, 'download_url': self.backend.download_url(identifier, 120)})
        return {'receipts': receipts}

    def close(self, body):
        row = self.proof(body, 'close')
        require(body.get('confirm') is True, 'explicit_closure_required')
        require(row['state'] == 'issued', 'verification_used', 409)
        require(self.backend.replace(row | {'state': 'completed'}, row['version']), 'verification_used', 409)
        pk = 'subject:' + row['subject']; moment = int(self.clock())
        for _ in range(5):
            previous = self.backend.get(pk)
            value = {'pk': pk, 'kind': 'subject', 'subject': row['subject'], 'closed_at': moment, 'version': 1}
            if previous is None:
                if self.backend.create(value): break
            elif self.backend.replace(value, previous['version']): break
        else: raise Rejected('concurrent_request', 409)
        self.cleanup_subject(row['subject'], moment)
        return {'status': 'closure_recorded', 'closed_at': utc(moment),
                'receipt_retention_until': utc(retention_end(moment))}

    def cleanup_subject(self, subject, moment):
        closed = self.closed_at(subject)
        for record in self.backend.for_subject(subject):
            if record['kind'] != 'receipt': continue
            if closed and record['created_at'] <= closed and record['state'] == 'pending':
                self.backend.revoke_upload(record['pk'].split(':', 1)[1])
                self.backend.delete(record['pk'], record['version'])
                continue
            if closed and record['created_at'] <= closed and record['state'] == 'active':
                updated = record | {'state': 'closed', 'closed_at': closed, 'expires_at': retention_end(closed)}
                updated.pop('ttl', None)
                if not self.backend.replace(updated, record['version']): continue
                record = updated | {'version': record['version'] + 1}
            if record['state'] == 'closed' and record['expires_at'] <= moment:
                self.backend.delete_receipt(record['pk'].split(':', 1)[1])
                self.backend.delete(record['pk'], record['version'])

    def cleanup(self):
        moment = int(self.clock())
        for record in self.backend.receipts():
            if record['state'] == 'committing':
                self.complete_committed(record)
            elif record['state'] == 'pending' and record['expires_at'] <= moment:
                self.backend.delete(record['pk'], record['version'])
        for subject in self.backend.subjects():
            self.cleanup_subject(subject['subject'], moment)
            if retention_end(subject['closed_at']) <= moment:
                if not any(r['kind'] == 'receipt' for r in self.backend.for_subject(subject['subject'])):
                    self.backend.delete(subject['pk'], subject['version'])
        return {'status': 'cleanup_completed'}

    def route(self, path, body, source_ip='unknown'):
        routes = {'/verification': self.verification, '/uploads': self.uploads, '/finalize': self.finalize,
                  '/receipts': self.access, '/close': self.close}
        if path == '/challenges': return self.challenge(body, source_ip)
        require(path in routes, 'not_found', 404)
        return routes[path](body)
