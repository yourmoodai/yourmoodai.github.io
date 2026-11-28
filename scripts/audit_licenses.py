"""Inventory declared source licenses without asserting relicensing authority."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--savior', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); root = args.savior.resolve()
    metadata = json.loads(subprocess.check_output(['cargo', 'metadata', '--locked', '--offline',
        '--format-version', '1'], cwd=root, text=True))
    lock = json.loads((root / 'package-lock.json').read_text())
    runtime = {k: v for k, v in lock['packages'].items() if k and not v.get('dev') and not v.get('optional')}
    inputs = ['Cargo.toml', 'Cargo.lock', 'package.json', 'package-lock.json', 'LICENSE', 'NOTICE',
              'THIRD-PARTY-NOTICES.md', 'models/catalog.json', 'models/NOTICE', 'scripts/release/package.py']
    report = {'schema': 1, 'status': 'source_inventory_only', 'relicensing_authority': 'not_established_by_tool',
        'artifact_qualification': 'not_performed',
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        'working_tree': 'modified' if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip() else 'clean',
        'input_sha256': {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in inputs},
        'rust': [{'name': p['name'], 'version': p['version'], 'license': p.get('license'),
                  'workspace': p['id'] in metadata['workspace_members']} for p in metadata['packages']],
        'npm_production': [{'path': p, 'version': value.get('version'), 'license': value.get('license')}
                           for p, value in runtime.items()],
        'manual_review': ['Company rights records and imported-source provenance',
                          'Anthropic SDK custom licensing and redistribution conditions',
                          'NOTICE section 7 attribution and source-availability wording',
                          'Actual assembled artifacts, native libraries, Node and model/asset notices']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'rust_packages': len(report['rust']),
                      'production_npm_packages': len(runtime), 'relicensing_approved': False}))


if __name__ == '__main__': main()
