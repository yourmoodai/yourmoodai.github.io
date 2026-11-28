# Official binary licensing audit

## Finding

A separate license for original company-owned code is potentially feasible, but
the complete Savior bundle is not cleared for a different licensing offer.
Current software and artifacts remain AGPL-3.0-or-later. No agreement in this
change adds restrictive binary terms or withdraws an existing grant.

The operator confirmed company ownership of original code. Git history contains
three author identities named Adam Williams, but neither that history nor a
copyright notice proves corporate assignment or permissions for imported code.
Identify the supporting rights records before approving a new offer.

## Source evidence

The initial audit inspected Savior revision
`a8650cdecd7fe3bc9c37383eba645ab7a22632c5` and its working-tree inputs. Reproduce
the current declared-license inventory with:

```sh
uv run --python 3.12 --with-requirements requirements.txt python scripts/audit_licenses.py \
  --savior ../savior --output .cache/legal/license-inventory.json
```

- `Cargo.toml:9`, `package.json:7`, `.claude-plugin/plugin.json:14`, and `NOTICE:4`
  declare AGPL-3.0-or-later. Six Rust workspace packages carry that declaration.
  The inspected external Rust declarations did not reveal an obvious mandatory
  third-party copyleft blocker; declarations alone do not establish the contents
  or licensing of every native binary. For example, the `r-efi` declarations offer
  MIT or Apache alternatives alongside LGPL rather than requiring LGPL.
- `scripts/release/package.py:95` copies hooks, bridge code, models, and the plugin
  manifest; line 100 installs production npm dependencies. The bundle therefore
  needs a production dependency and actual-artifact audit, not just a Rust scan.
- `@anthropic-ai/claude-agent-sdk` has a custom license pointer rather than an
  ordinary permissive declaration. Its installed `LICENSE.md:1` reserves rights
  and refers to [Anthropic's legal agreements](https://code.claude.com/docs/en/legal-and-compliance).
  `bridge/review.mjs:57` imports its session-reading functions. Verify the exact
  installed SDK and any included CLI/native material before redistribution or
  an alternative bundle license. This observation is not a finding that the
  current use violates those agreements.
- `models/NOTICE:1` and `models/catalog.json` identify separate Apache-2.0 model
  artifacts. Their notices and provenance must remain separate from any company
  license; model licensing does not license external datasets.
- Imported reviewer and commit-check tooling retains MIT licenses. Check actual
  source provenance and bundle membership rather than assuming all repository
  material is original company code. Node carries its own bundled license file.

## Existing notice language

`NOTICE:11` requires attribution in several locations, and `NOTICE:19` broadly
describes modified versions as requiring publicly available source. Review those
terms against AGPL sections 7, 10, and 13. The license distinguishes conveying
copies, covered network interaction, and private use; an upstream notice should
not overstate those duties or add prohibited further restrictions.

This audit preserves the existing notice pending a specific correction decision.
It does not treat that wording as approval to impose additional end-user terms.

## Decision before a different offer

Obtain the rights records, resolve custom third-party distribution terms,
inventory actual supported-platform artifacts, and approve a precise alternative
license and its relationship to the AGPL source offer. Keep all applicable
third-party notices. The [GNU licensing FAQ](https://www.gnu.org/licenses/gpl-faq.html#ReleaseUnderGPLAndNF)
explains the copyright-holder basis for parallel licensing; the
[AGPL](https://www.gnu.org/licenses/agpl-3.0.html) governs recipients' existing rights.

The adoption register's `licensing_audit` check stays pending. This report is
source-based technical due diligence, not a legal clearance or completed
installed-artifact review.
