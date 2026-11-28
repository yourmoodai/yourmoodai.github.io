import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import legal
from release_control import approval, configured, new_release


class Legal(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        for name in ['legal', 'docs/legal']:
            shutil.copytree(ROOT / name, self.root / name)
        for name in ['index.html', 'style.css']:
            shutil.copy2(ROOT / name, self.root / name)
        self.patch = mock.patch.object(legal.subprocess, 'check_output', return_value='a' * 40 + '\n')
        self.patch.start(); self.addCleanup(self.patch.stop); self.addCleanup(self.temp.cleanup)
        self.output = self.root / 'public'
        self.manifest = legal.build(self.output, 'v1', '2030-01-01T00:00:00Z', root=self.root)
        self.review = {'schema': 1, 'manifest_sha256': legal.digest(legal.encoded(self.manifest)),
            'approver': 'maintainer', 'run_url': 'https://github.com/example/actions/runs/1',
            'approval_observed_at': '2029-12-01T00:00:00Z'}
    def fetch(self, url):
        path = url.removeprefix(legal.ORIGIN).lstrip('/')
        return (self.output / path).read_bytes()
    def observe(self):
        return legal.observe_publication(self.manifest, self.review, self.fetch, '2029-12-02T00:00:00Z')
    def test_only_allowlisted_public_assets_are_built(self):
        self.assertFalse((self.output / 'docs').exists())
        self.assertFalse((self.output / 'infra').exists())
        legal.check_site(self.output, self.manifest)
        (self.output / 'private-email.txt').write_text('synthetic private content')
        with self.assertRaisesRegex(ValueError, 'unexpected_public_file'): legal.check_site(self.output, self.manifest)
    def test_exact_content_and_approval_required(self):
        self.assertEqual(self.observe()['status'], 'publication_observed')
        changed = copy.deepcopy(self.review); changed['manifest_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'approval_binding_failed'):
            legal.observe_publication(self.manifest, changed, self.fetch)
        (self.output / 'legal/privacy/index.html').write_text('altered')
        with self.assertRaisesRegex(ValueError, 'public_content_mismatch'): self.observe()
    def test_late_publication_cannot_be_backdated(self):
        with self.assertRaisesRegex(ValueError, 'publication_deadline_missed'):
            legal.observe_publication(self.manifest, self.review, self.fetch, '2030-01-02T00:00:00Z')
        receipt = self.observe()
        self.assertEqual(legal.effective_status(receipt, '2029-12-03T00:00:00Z')['status'], 'scheduled')
        self.assertEqual(legal.effective_status(receipt, '2030-01-01T00:00:00Z')['status'], 'declared_effective_date_reached')
        self.assertEqual(receipt['legal_enforceability'], 'not_determined')
        self.assertEqual(receipt['individual_acceptance'], 'not_established')
        with mock.patch.object(legal, 'now', side_effect=['2029-12-31T23:59:59Z', '2030-01-01T00:00:01Z']):
            with self.assertRaisesRegex(ValueError, 'publication_deadline_missed'):
                legal.observe_publication(self.manifest, self.review, self.fetch)
    def test_propagation_retry_never_retries_invalid_approval(self):
        with mock.patch.object(legal, 'observe_publication', side_effect=[ValueError('public_manifest_mismatch'), {'status': 'publication_observed'}]):
            with mock.patch.object(legal.time, 'sleep') as sleep:
                result = legal.await_publication(self.manifest, self.review)
                self.assertEqual(result['status'], 'publication_observed')
                sleep.assert_called_once_with(5)
        with mock.patch.object(legal, 'observe_publication', side_effect=ValueError('approval_binding_failed')):
            with mock.patch.object(legal.time, 'sleep') as sleep:
                with self.assertRaisesRegex(ValueError, 'approval_binding_failed'):
                    legal.await_publication(self.manifest, self.review)
                sleep.assert_not_called()
    def test_unresolved_or_stale_evidence_blocks_readiness(self):
        with self.assertRaisesRegex(ValueError, 'unresolved_adoption'): legal.check_adoption(self.root)
        path = self.root / 'docs/legal/adoption.json'; data = json.loads(path.read_text())
        evidence = self.root / 'docs/legal/evidence.txt'; evidence.write_text('synthetic operator record')
        for value in data['checks'].values():
            value.update(status='verified', reviewer='synthetic', checked_at='2026-01-01T00:00:00Z',
                         evidence=[{'path': 'docs/legal/evidence.txt', 'sha256': legal.digest(evidence.read_bytes())}])
        path.write_text(json.dumps(data)); legal.check_adoption(self.root)
        evidence.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'evidence_mismatch'): legal.check_adoption(self.root)
    def test_archived_version_survives_the_next_build(self):
        receipt = self.observe()
        with self.assertRaisesRegex(ValueError, 'archive_current_release'):
            new_release('v2', self.manifest, self.root)
        legal.archive_release(self.output, receipt, self.root / 'legal/history')
        new_release('v2', self.manifest, self.root)
        with self.assertRaisesRegex(ValueError, 'release_id_already_published'):
            new_release('v1', self.manifest, self.root)
        doc = self.manifest['documents'][0]; relative = doc['url'].removeprefix(legal.ORIGIN).strip('/') + '/index.html'
        original = (self.output / relative).read_bytes()
        (self.root / 'legal/website-privacy.md').write_text('# New text\n')
        legal.build(self.output, 'v2', root=self.root)
        self.assertEqual((self.output / relative).read_bytes(), original)
        with self.assertRaisesRegex(ValueError, 'release_id_already_archived'):
            legal.build(self.output, 'v1', root=self.root)
    def test_placeholders_and_source_escape_are_rejected(self):
        p = self.root / 'legal/website-privacy.md'; p.write_text('# Privacy\nREVIEW\n')
        with self.assertRaisesRegex(ValueError, 'editorial_placeholder'): legal.documents(self.root)
        p.write_text('# Privacy\n')
        catalog_path = self.root / 'legal/documents.json'; catalog = json.loads(catalog_path.read_text())
        catalog['documents'][0]['source'] = 'docs/legal/privacy-critique.md'; catalog_path.write_text(json.dumps(catalog))
        with self.assertRaisesRegex(ValueError, 'unexpected_document_source'): legal.documents(self.root)
    def test_human_review_is_bound_to_manifest_digest(self):
        sha = legal.digest(legal.encoded(self.manifest))
        review = {'state': 'approved', 'comment': 'approve legal ' + sha,
                  'environments': [{'name': 'legal-publication'}], 'user': {'login': 'maintainer', 'id': 42}}
        value = approval(self.manifest, [review], 123, '2029-12-01T00:00:00Z')
        self.assertEqual(value['approver'], 'maintainer')
        review['comment'] = 'approved'
        with self.assertRaisesRegex(ValueError, 'exact_manifest_approval'): approval(self.manifest, [review], 123)
        with self.assertRaisesRegex(ValueError, 'pages_actions_source_required'): configured({'build_type': 'legacy'}, {})
        with self.assertRaisesRegex(ValueError, 'required_environment_reviewers_missing'): configured({'build_type': 'workflow'}, {})


if __name__ == '__main__': unittest.main()
