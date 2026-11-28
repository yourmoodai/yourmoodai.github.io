"""Build agreement pages and verify publication and private receipt evidence."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://yourmood.ai'
CHECKS = {'company_facts', 'email_operations', 'retention_operations',
          'jurisdiction_review', 'licensing_audit', 'privacy_runtime',
          'receipt_live_verification'}


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(',', ':'), allow_nan=False) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def timestamp(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        raise ValueError('utc_timestamp_required')
    return datetime.fromisoformat(value[:-1] + '+00:00')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def load(path):
    return json.loads(Path(path).read_text())


def safe_path(root, relative):
    if not isinstance(relative, str) or relative.startswith('/'):
        raise ValueError('invalid_relative_path')
    candidate = root / relative
    target = candidate.resolve()
    if not target.is_relative_to(root.resolve()) or any(p.is_symlink() for p in [candidate, *candidate.parents] if p != root.parent):
        raise ValueError('invalid_relative_path')
    return target


def check_adoption(root=ROOT):
    data = load(root / 'docs/legal/adoption.json')
    if data.get('schema') != 1 or set(data.get('checks', {})) != CHECKS:
        raise ValueError('invalid_adoption_record')
    for name, record in data['checks'].items():
        if record.get('status') != 'verified' or not record.get('reviewer'):
            raise ValueError('unresolved_adoption:' + name)
        timestamp(record['checked_at'])
        if not record.get('evidence'):
            raise ValueError('missing_evidence:' + name)
        for evidence in record['evidence']:
            path = safe_path(root, evidence['path'])
            if not path.is_file() or digest(path.read_bytes()) != evidence['sha256']:
                raise ValueError('evidence_mismatch:' + name)
    return data


def documents(root=ROOT):
    data = load(root / 'legal/documents.json')
    if data.get('schema') != 1 or len(data.get('documents', [])) != 4:
        raise ValueError('invalid_document_catalog')
    ids, paths = set(), set()
    for doc in data['documents']:
        if not re.fullmatch(r'[a-z][a-z0-9-]+', doc['id']) or doc['id'] in ids:
            raise ValueError('invalid_document_id')
        if not re.fullmatch(r'/legal/(?:[a-z-]+/)+', doc['path']) or doc['path'] in paths:
            raise ValueError('invalid_document_path')
        if not re.fullmatch(r'\d+\.\d+\.\d+', doc['version']):
            raise ValueError('invalid_document_version')
        source = safe_path(root, doc['source'])
        if doc['source'] != 'legal/' + doc['id'] + '.md':
            raise ValueError('unexpected_document_source')
        text = source.read_text()
        if re.search(r'\bREVIEW\b|Draft for review|\[REVIEW|Proposed commitment', text):
            raise ValueError('editorial_placeholder:' + doc['id'])
        ids.add(doc['id']); paths.add(doc['path'])
    return data['documents']


def page(title, body, path, version='', effective_at=None, stylesheet='/style.css'):
    status = ('Declared effective date: ' + effective_at) if effective_at else 'Prepared for release; not yet effective'
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="referrer" content="no-referrer">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'style-src \'self\'; img-src \'self\'; base-uri \'none\'; form-action \'none\'">'
            f'<title>{escape(title)} — Your Mood, Inc.</title>'
            f'<link rel="canonical" href="{ORIGIN}{escape(path)}">'
            f'<link rel="stylesheet" href="{escape(stylesheet)}"></head><body>'
            '<a class="skip" href="#main">Skip to content</a>'
            '<header><a class="brand" href="/">yourmood.ai</a>'
            '<nav><a href="/legal/">Legal</a></nav></header>'
            f'<main id="main" class="legal"><p class="label">{escape(status)}'
            f'{(" · Version " + escape(version)) if version else ""}</p>{body}</main>'
            '<footer><a href="/legal/">Agreements and publication evidence</a>'
            '<a href="mailto:support@yourmood.ai">support@yourmood.ai</a></footer>'
            '</body></html>\n').encode()


def build(output, release_id='candidate', effective_at=None, receipt_api=None, root=ROOT):
    from markdown_it import MarkdownIt
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}', release_id):
        raise ValueError('invalid_release_id')
    if effective_at:
        timestamp(effective_at)
    if receipt_api:
        url = urlsplit(receipt_api)
        if url.scheme != 'https' or not re.fullmatch(r'[a-z0-9]+\.execute-api\.us-east-1\.amazonaws\.com', url.netloc) or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError('invalid_receipt_api')
        receipt_api = receipt_api.rstrip('/')
    candidate = Path(output).absolute()
    if candidate.is_symlink(): raise ValueError('unsafe_build_directory')
    output = candidate.resolve()
    if not output.is_relative_to(root.resolve()): raise ValueError('unsafe_build_directory')
    if output == root.resolve() or not (output == (root / 'public').resolve() or output.is_relative_to((root / '.cache').resolve())):
        raise ValueError('unsafe_build_directory')
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for name in ['index.html', 'style.css']:
        shutil.copy2(root / name, output / name)
    parser = MarkdownIt('commonmark', {'html': False}).enable('table')
    catalog = documents(root)
    manifest = {'schema': 1, 'release_id': release_id, 'effective_at': effective_at,
                'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
                'adoption_sha256': digest((root / 'docs/legal/adoption.json').read_bytes()),
                'documents': [], 'assets': {}}
    history = root / 'legal/history'
    if history.exists():
        for directory in sorted(history.iterdir()):
            if not directory.is_dir() or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}', directory.name):
                raise ValueError('invalid_history_directory')
            previous = load(directory / 'archive.json')
            if previous['release_id'] != directory.name or previous['release_id'] == release_id:
                raise ValueError('release_id_already_archived')
            for relative, expected in previous['files'].items():
                source = safe_path(directory, relative)
                if not source.is_file() or digest(source.read_bytes()) != expected:
                    raise ValueError('archive_integrity_failed')
                target = output / 'legal/versions' / directory.name / relative
                target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
    version_directory = output / 'legal/versions' / release_id
    version_directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / 'style.css', version_directory / 'style.css')
    for doc in catalog:
        source = safe_path(root, doc['source']).read_bytes()
        archive = f'/legal/versions/{release_id}/{doc["id"]}/'
        rendered = page(doc['title'], parser.render(source.decode()), archive, doc['version'], effective_at,
                        f'/legal/versions/{release_id}/style.css')
        for path in [doc['path'], archive]:
            dest = output / path.strip('/') / 'index.html'
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(rendered)
        manifest['documents'].append({k: doc[k] for k in ['id', 'title', 'version']} |
            {'sha256': digest(rendered), 'source_sha256': digest(source),
             'url': ORIGIN + archive, 'canonical_url': ORIGIN + doc['path']})
    links = ''.join(f'<li><a href="{d["canonical_url"]}">{escape(d["title"])}</a> · '
                    f'<a href="{d["url"]}">Version {d["version"]}</a></li>' for d in manifest['documents'])
    body = ('<h1>Agreements</h1><ul>' + links + '</ul><p>Publication evidence records '
            'approved text and observed availability. It is separate from legal enforceability '
            'and individual acceptance.</p><p><a href="/legal/manifest.json">Release manifest</a> · '
            '<a href="/legal/receipts/">Private acceptance receipts</a></p>')
    if effective_at:
        body += ('<p><a href="https://github.com/yourmoodai/yourmoodai.github.io/releases/tag/legal-'
                 + release_id + '">Publication receipt and verification bundles</a> '
                 'are available after successful publication verification.</p>')
    (output / 'legal/index.html').write_bytes(page('Agreements', body, '/legal/', effective_at=effective_at))
    receipts = output / 'legal/receipts'
    receipts.mkdir()
    for name in ['index.html', 'app.js']:
        shutil.copy2(root / 'legal/receipts' / name, receipts / name)
    keys = load(root / 'legal/keys/index.json')
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    if keys.get('schema') != 1: raise ValueError('invalid_key_catalog')
    seen_keys = set()
    for key in keys['keys']:
        if not re.fullmatch(r'[a-zA-Z0-9._-]+\.pem', key['file']):
            raise ValueError('invalid_key_file')
        source = safe_path(root / 'legal/keys', key['file'])
        if key['key_id'] in seen_keys or digest(source.read_bytes()) != key['sha256']:
            raise ValueError('key_catalog_integrity_failed')
        seen_keys.add(key['key_id'])
        public_key = serialization.load_pem_public_key(source.read_bytes())
        if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 2048:
            raise ValueError('unsupported_receipt_key')
        dest = safe_path(output / 'legal/keys', key['file'])
        dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, dest)
    (output / 'legal/keys').mkdir(exist_ok=True)
    (output / 'legal/keys/index.json').write_bytes(encoded(keys))
    (output / 'legal/receipt-config.json').write_bytes(encoded({'api': receipt_api, 'keys': '/legal/keys/index.json'}))
    for path in sorted(output.rglob('*')):
        if path.is_file():
            manifest['assets']['/' + path.relative_to(output).as_posix()] = digest(path.read_bytes())
    (output / 'legal/manifest.json').write_bytes(encoded(manifest))
    check_site(output, manifest)
    return manifest


def check_site(output, manifest):
    from html.parser import HTMLParser
    class Links(HTMLParser):
        def __init__(self, text):
            super().__init__(); self.links = []; self.ids = set(); self.feed(text)
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if 'id' in attrs: self.ids.add(attrs['id'])
            for key in ['href', 'src']:
                if key in attrs: self.links.append(attrs[key])
    output = Path(output)
    for path, sha in manifest['assets'].items():
        if digest((output / path.lstrip('/')).read_bytes()) != sha:
            raise ValueError('artifact_mismatch')
    pages = {p: Links(p.read_text()) for p in output.rglob('*.html')}
    for path, parsed in pages.items():
        for link in parsed.links:
            url = urlsplit(link)
            if url.scheme in ('mailto',) or (url.netloc and url.netloc != 'yourmood.ai'): continue
            dest = output / url.path.lstrip('/') if url.path.startswith('/') or url.netloc else path.parent / url.path
            if not url.path: dest = path
            if dest.is_dir(): dest /= 'index.html'
            if not dest.is_file(): raise ValueError('missing_site_link:' + link)
            if url.fragment and dest.suffix == '.html' and url.fragment not in pages[dest].ids:
                raise ValueError('missing_site_anchor:' + link)
    allowed = set(manifest['assets']) | {'/legal/manifest.json'}
    actual = {'/' + p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}
    if allowed != actual or any(p.startswith(('/docs/', '/infra/', '/tests/', '/.git/')) for p in actual):
        raise ValueError('unexpected_public_file')


def release_ready(manifest, root=ROOT):
    check_adoption(root)
    if not load(root / 'legal/keys/index.json').get('keys'):
        raise ValueError('public_receipt_key_required')
    if manifest['adoption_sha256'] != digest((root / 'docs/legal/adoption.json').read_bytes()):
        raise ValueError('stale_adoption')
    if not manifest['effective_at'] or timestamp(manifest['effective_at']) <= timestamp(now()):
        raise ValueError('future_effective_date_required')
    if manifest['release_id'] == 'candidate': raise ValueError('release_id_required')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if manifest['source_commit'] != head or subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip():
        raise ValueError('clean_exact_revision_required')


def observe_publication(manifest, approval, fetch=None, observed_at=None):
    supplied_time = observed_at
    observed_at = observed_at or now()
    sha = digest(encoded(manifest))
    if approval.get('schema') != 1 or approval.get('manifest_sha256') != sha or not approval.get('approver') or not approval.get('run_url'):
        raise ValueError('approval_binding_failed')
    if not manifest.get('effective_at'):
        raise ValueError('effective_date_required')
    if timestamp(approval['approval_observed_at']) > timestamp(observed_at): raise ValueError('invalid_approval_time')
    if timestamp(approval['approval_observed_at']) >= timestamp(manifest['effective_at']): raise ValueError('late_approval')
    if timestamp(observed_at) > timestamp(manifest['effective_at']):
        raise ValueError('publication_deadline_missed')
    if fetch is None:
        def fetch(url):
            request = urllib.request.Request(url, headers={'Cache-Control': 'no-cache', 'Accept-Encoding': 'identity'})
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status != 200 or response.url != url:
                    raise ValueError('unexpected_public_response')
                data = response.read(4 * 1024 * 1024 + 1) # 4 MiB maximum public asset
                if len(data) > 4 * 1024 * 1024: raise ValueError('public_asset_too_large')
                return data
    for path, expected in manifest['assets'].items():
        if not path.startswith('/') or '..' in path.split('/') or urlsplit(path).netloc:
            raise ValueError('invalid_public_path')
        if digest(fetch(ORIGIN + path)) != expected:
            raise ValueError('public_content_mismatch:' + path)
    if fetch(ORIGIN + '/legal/manifest.json') != encoded(manifest):
        raise ValueError('public_manifest_mismatch')
    # Availability is established after every asset has been observed.
    observed_at = supplied_time or now()
    if timestamp(observed_at) > timestamp(manifest['effective_at']):
        raise ValueError('publication_deadline_missed')
    return {'schema': 1, 'status': 'publication_observed', 'manifest_sha256': sha,
            'release_id': manifest['release_id'], 'source_commit': manifest['source_commit'],
            'first_observed_at': observed_at, 'declared_effective_at': manifest['effective_at'],
            'approval': approval, 'documents': manifest['documents'], 'assets': manifest['assets'],
            'legal_enforceability': 'not_determined', 'individual_acceptance': 'not_established'}


def await_publication(manifest, approval, attempts=12):
    for attempt in range(attempts):
        try:
            return observe_publication(manifest, approval)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            transient = isinstance(error, (urllib.error.URLError, TimeoutError)) or str(error).startswith(
                ('public_content_mismatch:', 'public_manifest_mismatch', 'unexpected_public_response'))
            if not transient or attempt == attempts - 1:
                raise
            if timestamp(now()) >= timestamp(manifest['effective_at']):
                raise ValueError('publication_deadline_missed') from error
            time.sleep(5)


def effective_status(receipt, at=None):
    if receipt.get('status') != 'publication_observed': raise ValueError('publication_not_observed')
    if timestamp(receipt['first_observed_at']) > timestamp(receipt['declared_effective_at']):
        raise ValueError('publication_deadline_missed')
    return {'status': 'declared_effective_date_reached' if timestamp(at or now()) >= timestamp(receipt['declared_effective_at']) else 'scheduled',
            'publication_receipt_sha256': digest(encoded(receipt)),
            'legal_enforceability': 'not_determined'}


def verify_receipt(envelope, key_file, expected_key_id):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    if envelope.get('algorithm') != 'RSASSA_PSS_SHA_256' or envelope.get('key_id') != expected_key_id:
        raise ValueError('untrusted_receipt_key')
    payload = base64.b64decode(envelope['payload'], validate=True)
    signature = base64.b64decode(envelope['signature'], validate=True)
    key = serialization.load_pem_public_key(Path(key_file).read_bytes())
    key.verify(signature, payload, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
    value = json.loads(payload)
    if value.get('schema') != 1 or value.get('action') != 'accept': raise ValueError('invalid_receipt')
    return value


def archive_release(output, receipt, destination):
    output, destination = Path(output), Path(destination)
    manifest = load(output / 'legal/manifest.json')
    if receipt.get('status') != 'publication_observed' or receipt['manifest_sha256'] != digest(encoded(manifest)):
        raise ValueError('archive_publication_binding_failed')
    check_site(output, manifest)
    source = output / 'legal/versions' / manifest['release_id']
    dest = destination / manifest['release_id']
    if dest.exists(): raise ValueError('archive_already_exists')
    files = {p.relative_to(source).as_posix(): digest(p.read_bytes()) for p in source.rglob('*') if p.is_file()}
    shutil.copytree(source, dest)
    (dest / 'archive.json').write_bytes(encoded({'schema': 1, 'release_id': manifest['release_id'],
        'publication_receipt_sha256': digest(encoded(receipt)), 'files': files}))


def verify_public_receipt(path):
    receipt = load(path)
    subprocess.run(['gh', 'attestation', 'verify', str(path), '--repo', 'yourmoodai/yourmoodai.github.io',
        '--signer-workflow', 'yourmoodai/yourmoodai.github.io/.github/workflows/legal-release.yml',
        '--source-digest', receipt['source_commit'], '--source-ref', 'refs/heads/main'], check=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('build'); p.add_argument('--output', type=Path, default=ROOT / 'public')
    p.add_argument('--release-id', default='candidate'); p.add_argument('--effective-at'); p.add_argument('--receipt-api')
    sub.add_parser('check')
    p = sub.add_parser('ready'); p.add_argument('--manifest', type=Path, required=True)
    p = sub.add_parser('observe'); p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--approval', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('effective'); p.add_argument('--receipt', type=Path, required=True)
    p = sub.add_parser('verify-receipt'); p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--public-key', type=Path, required=True); p.add_argument('--key-id', required=True)
    p = sub.add_parser('archive'); p.add_argument('--site', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True); p.add_argument('--destination', type=Path, default=ROOT / 'legal/history')
    args = parser.parse_args()
    if args.command == 'build':
        build(args.output, args.release_id, args.effective_at, args.receipt_api)
        print('legal_site_built')
    elif args.command == 'check':
        documents(); check_site(ROOT / 'public', load(ROOT / 'public/legal/manifest.json')); print('legal_site_checked')
    elif args.command == 'ready': release_ready(load(args.manifest)); print('release_ready')
    elif args.command == 'observe':
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(encoded(await_publication(load(args.manifest), load(args.approval))))
        print('publication_observed')
    elif args.command == 'effective': print(json.dumps(effective_status(verify_public_receipt(args.receipt))))
    elif args.command == 'archive':
        archive_release(args.site, verify_public_receipt(args.receipt), args.destination); print('release_archived_for_review')
    else:
        verify_receipt(load(args.receipt), args.public_key, args.key_id); print('receipt_signature_verified')


if __name__ == '__main__':
    try: main()
    except (ValueError, KeyError) as error:
        print(str(error), file=sys.stderr); raise SystemExit(1)
