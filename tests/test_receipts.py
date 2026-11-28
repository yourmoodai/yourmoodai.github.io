import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'infra/receipts'))
sys.path.insert(0, str(ROOT / 'scripts'))
from service import ReceiptService, Rejected, encode, sha, retention_end, epoch, utc
from legal import verify_receipt


class Backend:
    def __init__(self):
        self.rows, self.uploads, self.final, self.mail, self.rates = {}, {}, {}, [], {}
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.sign_count = 0; self.fail_replace = False; self.fail_final = False
        self.active = {'schema': 1, 'publication_status': 'publication_observed',
            'effective_at': '2020-01-01T00:00:00Z', 'release_id': 'v1', 'documents': [
            {'id': 'terms', 'title': 'Receipt service terms', 'version': '1.0.0', 'sha256': 'a' * 64,
             'url': 'https://yourmood.ai/legal/versions/v1/terms/'}]}
    def registry(self): return copy.deepcopy(self.active)
    def get(self, pk): return copy.deepcopy(self.rows.get(pk))
    def create(self, row):
        if row['pk'] in self.rows: return False
        self.rows[row['pk']] = copy.deepcopy(row); return True
    def replace(self, row, version):
        if self.fail_replace: self.fail_replace = False; raise RuntimeError('synthetic interrupted metadata write')
        if self.rows[row['pk']]['version'] != version: return False
        self.rows[row['pk']] = copy.deepcopy(row | {'version': version + 1}); return True
    def commit_acceptance(self, row, moment, version):
        closed = self.rows.get('subject:' + row['subject'], {}).get('closed_at', 0)
        if closed >= row['created_at']: return None
        current = self.get(row['pk'])
        if current['state'] == 'committing': return current
        if current['version'] != row['version']: return None
        value = row | {'state': 'committing', 'committed_at': moment, 'upload_version': version}
        self.replace(value, row['version']); return self.get(row['pk'])
    def delete(self, pk, version):
        if self.rows.get(pk, {}).get('version') != version: return False
        self.rows.pop(pk, None); return True
    def rate_limit(self, key, moment, limit):
        slot = (key, moment // 3600); count = self.rates.get(slot, 0)
        if count >= limit: raise Rejected('rate_limited', 429)
        self.rates[slot] = count + 1
    def for_subject(self, subject): return [copy.deepcopy(r) for r in self.rows.values() if r.get('subject') == subject]
    def subjects(self): return [copy.deepcopy(r) for r in self.rows.values() if r['kind'] == 'subject']
    def receipts(self): return [copy.deepcopy(r) for r in self.rows.values() if r['kind'] == 'receipt']
    def send_email(self, email, identifier, token, purpose, document): self.mail.append((email, identifier, token, purpose))
    def upload_url(self, identifier, checksum, length, seconds):
        self.expected = (identifier, checksum, length, seconds)
        return 'https://synthetic.s3.amazonaws.com/pending/' + identifier
    def pending(self, identifier):
        if identifier not in self.uploads: raise Rejected('upload_missing', 409)
        return self.uploads[identifier], 'synthetic-version'
    def sign(self, data):
        self.sign_count += 1
        return self.key.sign(data, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256()), 'synthetic-key'
    def store_final(self, identifier, value):
        if self.fail_final: self.fail_final = False; raise RuntimeError('synthetic storage interruption')
        self.final.setdefault(identifier, value); return self.final[identifier]
    def final_receipt(self, identifier): return self.final[identifier]
    def existing_final(self, identifier): return self.final.get(identifier)
    def download_url(self, identifier, seconds): return f'https://synthetic.s3.amazonaws.com/receipts/{identifier}?expires={seconds}'
    def delete_receipt(self, identifier): self.final.pop(identifier, None)
    def revoke_upload(self, identifier): self.uploads.setdefault(identifier, b'{}')


class Receipts(unittest.TestCase):
    def setUp(self):
        self.backend = Backend(); self.time = 1800000000
        self.service = ReceiptService(self.backend, lambda: self.time)
    def proof(self, purpose='accept', email='person@example.test'):
        body = {'email': email, 'purpose': purpose}
        if purpose == 'accept': body['document_id'] = 'terms'
        self.service.challenge(body, '192.0.2.1')
        _, identifier, token, _ = self.backend.mail[-1]
        return {'challenge': identifier, 'token': token}
    def upload(self, proof):
        result = self.service.uploads(proof | {'confirm': True})
        self.backend.uploads[result['receipt_id']] = result['payload'].encode()
        return result
    def accepted(self):
        proof = self.proof(); self.upload(proof)
        return proof, self.service.finalize(proof)
    def rejected(self, code, callback):
        with self.assertRaises(Rejected) as raised: callback()
        self.assertEqual(raised.exception.code, code)
    def test_verification_and_opening_links_never_accept(self):
        proof = self.proof()
        info = self.service.verification(proof)
        self.assertEqual(info['purpose'], 'accept')
        self.assertEqual(self.backend.final, {})
        self.rejected('explicit_acceptance_required', lambda: self.service.uploads(proof))
        self.assertNotIn('token', self.service.challenge({'email': 'other@example.test', 'purpose': 'access'}, '192.0.2.2'))
    def test_signature_and_exact_document_binding(self):
        proof, envelope = self.accepted()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'public.pem'
            path.write_bytes(self.backend.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
            value = verify_receipt(envelope, path, 'synthetic-key')
            self.assertEqual(value['agreement']['sha256'], 'a' * 64)
            self.assertEqual(value['verification']['identity_assurance'], 'mailbox_control_only')
            modified = envelope | {'payload': base64.b64encode(b'{"action":"forged"}').decode()}
            with self.assertRaises(Exception): verify_receipt(modified, path, 'synthetic-key')
            with self.assertRaises(ValueError): verify_receipt(envelope, path, 'untrusted-key')
        self.assertEqual(self.service.finalize(proof), envelope)
        self.assertEqual(self.backend.sign_count, 1)
    def test_expiry_wrong_token_and_wrong_purpose(self):
        proof = self.proof()
        self.rejected('verification_failed', lambda: self.service.uploads(proof | {'token': 'x' * 43, 'confirm': True}))
        self.rejected('wrong_verification_purpose', lambda: self.service.access(proof))
        self.time += 901
        self.rejected('verification_expired', lambda: self.service.uploads(proof | {'confirm': True}))
    def test_changed_agreement_and_future_effective_date(self):
        proof = self.proof()
        self.backend.active['documents'][0]['sha256'] = 'b' * 64
        self.rejected('agreement_changed', lambda: self.service.uploads(proof | {'confirm': True}))
        self.backend.active['effective_at'] = '2099-01-01T00:00:00Z'
        self.rejected('agreement_not_effective', lambda: self.proof(email='later@example.test'))
    def test_upload_not_trusted_even_after_checksum_claim(self):
        proof = self.proof(); upload = self.upload(proof)
        self.backend.uploads[upload['receipt_id']] = b'forged'
        self.rejected('upload_mismatch', lambda: self.service.finalize(proof))
        self.assertEqual(self.backend.sign_count, 0)
    def test_upload_retry_binds_the_same_payload(self):
        proof = self.proof(); first = self.upload(proof); self.time += 10
        second = self.service.uploads(proof | {'confirm': True})
        self.assertEqual(first['payload'], second['payload'])
        self.assertEqual(first['headers']['If-None-Match'], '*')
        self.assertEqual(first['sha256'], sha(first['payload'].encode()))
    def test_closed_service_rejects_pending_and_retains_signed_evidence(self):
        proof, envelope = self.accepted()
        self.time += 1
        pending = self.proof(email='pending@example.test'); upload = self.service.uploads(pending | {'confirm': True})
        self.time += 1
        closing = self.proof('close', 'pending@example.test')
        self.service.close(closing | {'confirm': True})
        self.assertEqual(self.backend.uploads[upload['receipt_id']], b'{}')
        self.rejected('receipt_unavailable', lambda: self.service.finalize(pending))
        self.time += 1
        closing = self.proof('close'); result = self.service.close(closing | {'confirm': True})
        self.assertIn(proof['challenge'], self.backend.final)
        self.assertEqual(result['status'], 'closure_recorded')
        self.time = retention_end(self.time) + 1
        self.service.cleanup()
        self.assertNotIn(proof['challenge'], self.backend.final)
    def test_access_is_scoped_and_single_use(self):
        proof, _ = self.accepted()
        stranger = self.proof('access', 'stranger@example.test')
        self.assertEqual(self.service.access(stranger), {'receipts': []})
        owner = self.proof('access')
        result = self.service.access(owner)
        self.assertEqual([r['receipt_id'] for r in result['receipts']], [proof['challenge']])
        self.rejected('verification_used', lambda: self.service.access(owner))
    def test_crash_after_commit_is_recovered_without_new_acceptance(self):
        proof = self.proof(); self.upload(proof); self.backend.fail_final = True
        with self.assertRaises(RuntimeError): self.service.finalize(proof)
        self.assertEqual(self.backend.get('receipt:' + proof['challenge'])['state'], 'committing')
        self.time += 2 * 86400
        self.service.cleanup()
        self.assertEqual(self.backend.get('receipt:' + proof['challenge'])['state'], 'active')
        self.assertIn(proof['challenge'], self.backend.final)
        receipt = json.loads(base64.b64decode(self.backend.final[proof['challenge']]['payload']))
        self.assertEqual(receipt['acceptance_committed_at'], utc(self.time - 2 * 86400))
    def test_retention_uses_calendar_years_including_leap_day(self):
        self.assertEqual(utc(retention_end(epoch('2024-02-29T12:00:00Z'))), '2027-02-28T12:00:00Z')
        self.assertEqual(utc(retention_end(epoch('2025-03-01T12:00:00Z'))), '2028-03-01T12:00:00Z')
    def test_cleanup_cannot_delete_a_concurrently_committed_receipt(self):
        proof = self.proof(); self.upload(proof)
        pending = self.backend.get('receipt:' + proof['challenge'])
        # Simulate cleanup reading a pending row just before finalization commits.
        self.backend.commit_acceptance(pending, self.time, 'synthetic-version')
        self.time += 86401
        original = self.backend.receipts
        self.backend.receipts = lambda: [pending]
        self.service.cleanup()
        self.backend.receipts = original
        self.assertEqual(self.backend.get(pending['pk'])['state'], 'committing')
        self.service.cleanup()
        self.assertEqual(self.backend.get(pending['pk'])['state'], 'active')
    def test_oversharing_and_spam_are_rejected(self):
        self.rejected('invalid_request', lambda: self.service.challenge({'email': 'a@example.test', 'purpose': 'access', 'prompt': 'synthetic'}, '192.0.2.1'))
        for _ in range(3): self.proof()
        self.rejected('rate_limited', self.proof)


if __name__ == '__main__': unittest.main()
