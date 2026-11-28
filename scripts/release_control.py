"""Bind a GitHub environment approval to the exact publication manifest."""
import argparse
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

from legal import ROOT, encoded, digest, load, now

REPOSITORY = 'yourmoodai/yourmoodai.github.io'
ENVIRONMENT = 'legal-publication'


def github(path):
    request = urllib.request.Request('https://api.github.com/repos/' + REPOSITORY + path,
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    with urllib.request.urlopen(request, timeout=20) as response: return json.load(response)


def configured(pages, environment):
    if pages.get('build_type') != 'workflow':
        raise ValueError('pages_actions_source_required')
    rules = environment.get('protection_rules', [])
    if not any(r.get('type') == 'required_reviewers' and r.get('reviewers') for r in rules):
        raise ValueError('required_environment_reviewers_missing')
    if environment.get('can_admins_bypass', True):
        raise ValueError('environment_bypass_must_be_disabled')


def new_release(release_id, current_manifest, root=ROOT):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}', release_id):
        raise ValueError('invalid_release_id')
    if current_manifest is None: return
    previous_id = current_manifest['release_id']
    if previous_id == release_id: raise ValueError('release_id_already_published')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}', previous_id):
        raise ValueError('invalid_previous_release_id')
    archive = root / 'legal/history' / previous_id
    if not (archive / 'archive.json').is_file():
        raise ValueError('archive_current_release_before_replacement')
    for path, expected in current_manifest['assets'].items():
        prefix = '/legal/versions/' + previous_id + '/'
        if path.startswith(prefix):
            relative = path[len(prefix):]
            if '..' in relative.split('/') or digest((archive / relative).read_bytes()) != expected:
                raise ValueError('previous_release_archive_mismatch')


def approval(manifest, reviews, run_id, observed_at=None):
    sha = digest(encoded(manifest))
    choices = [r for r in reviews if r.get('state') == 'approved'
        and any(e.get('name') == ENVIRONMENT for e in r.get('environments', []))
        and r.get('comment', '').strip() == 'approve legal ' + sha]
    if len(choices) != 1 or not choices[0].get('user', {}).get('login'):
        raise ValueError('exact_manifest_approval_required')
    return {'schema': 1, 'manifest_sha256': sha, 'approver': choices[0]['user']['login'],
            'approver_id': choices[0]['user']['id'], 'environment': ENVIRONMENT,
            'approval_observed_at': observed_at or now(),
            'time_basis': 'observed_after_environment_approval',
            'run_url': f'https://github.com/{REPOSITORY}/actions/runs/{run_id}'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['configuration', 'approval'])
    parser.add_argument('--manifest', type=Path); parser.add_argument('--output', type=Path)
    parser.add_argument('--release-id')
    args = parser.parse_args()
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('trusted_main_workflow_required')
    configured(github('/pages'), github('/environments/' + ENVIRONMENT))
    if args.command == 'configuration':
        if not args.release_id or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}', args.release_id):
            raise ValueError('invalid_release_id')
        try:
            github('/releases/tags/legal-' + args.release_id)
        except urllib.error.HTTPError as error:
            if error.code != 404: raise
        else: raise ValueError('release_id_already_exists')
        try:
            url = 'https://yourmood.ai/legal/manifest.json'
            with urllib.request.urlopen(url, timeout=20) as response:
                if response.url != url: raise ValueError('unexpected_public_manifest_redirect')
                data = response.read(4 * 1024 * 1024 + 1) # 4 MiB manifest limit
                if len(data) > 4 * 1024 * 1024: raise ValueError('public_manifest_too_large')
                current = json.loads(data)
        except urllib.error.HTTPError as error:
            if error.code != 404: raise
            current = None
        new_release(args.release_id, current)
    if args.command == 'approval':
        manifest = load(args.manifest)
        if manifest['source_commit'] != os.environ['GITHUB_SHA']:
            raise ValueError('revision_binding_failed')
        run_id = os.environ['GITHUB_RUN_ID']
        value = approval(manifest, github('/actions/runs/' + run_id + '/approvals'), run_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(encoded(value))
    print('release_control_verified')


if __name__ == '__main__': main()
