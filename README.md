# yourmoodai.github.io

Company website and agreement sources for Your Mood, Inc. The four agreements
live in `legal/`; internal decisions and adoption evidence live in `docs/legal/`.
Only the generated `public/` directory is eligible for deployment.

## Development

Install `uv` and run:

```sh
make check                 # build the candidate and check links and digests
make test                  # offline domain, adapter, and release-gate tests
make infrastructure-check  # validate the AWS SAM template
make preview               # http://127.0.0.1:1111
make debug                 # Python debugger on the candidate build
make profile               # local build memory measurements
```

Dependency locks include hashes. `.vscode/tasks.json` exposes the same commands.
Preview builds say “Prepared for release; not yet effective” and leave the
receipt service inactive. Building does not deploy, send email, or contact AWS.

## Publication

**Before merging this source, change GitHub Pages from branch/root publishing
to GitHub Actions.** A branch/root publisher could expose internal documentation.
Do not point Pages at the repository root. The release workflow checks this
setting and requires a protected `legal-publication` environment.

The ordinary check workflow only produces a preview. Publication requires a
separate manual dispatch, evidence checks, and approval of the exact manifest
digest. The [release guide](docs/legal/README.md) covers adoption, private
receipt-service setup, approval, evidence verification, and later revisions.
No agreement becomes effective merely because its source is committed.
