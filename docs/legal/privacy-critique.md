# Privacy critique


The assessment below records the policy review against Savior revision
`a8650cdecd7fe3bc9c37383eba645ab7a22632c5`. A successor implementation gates all
trace history on consent under ADR 13. Publication still requires its test and
release evidence; this source change is not a claim of activated protection.

Your Mood's proposed position is to minimize personal information collection
and exposure while keeping the person concerned able to inspect, export, and
delete their own managed information. Optional persisted local records and
remote analytics should default off. Remote analytics, if introduced, should
contain only minimal, content-free aggregates for a disclosed purpose.

These are selected **proposed commitments**. They are not a claim that every
current implementation or business practice already satisfies them. This
critique compares the initial policies and their [source evidence](README.md#evidence-map) with
selected sections of *The Architecture of Privacy* by Courtney Bowman, Ari
Gesher, John K. Grant, and Daniel Slate (O'Reilly, 2015).

## What the book contributes

The book treats privacy as a continuing responsibility for collection, access,
use, oversight, and disposal. Consent is one part of that responsibility. Our
specific default-off policy is a company choice informed by those principles;
the book is not authority for a claim that it mandates this exact policy.

| Selected reading | Principle used in this critique |
| --- | --- |
| Chapter 2, printed p. 23; PDF page 45 | Identify a use case before collecting data; retain only what it needs. Where aggregates suffice, avoid retaining source records. |
| Chapter 2, printed p. 27; PDF page 49 | Convenient collection of entire source objects can exceed the stated purpose. Policy language needs to match actual collection boundaries. |
| Chapter 5, printed pp. 39–40; PDF pages 61–62 | Separate powers and consider access below the application, including administrators and infrastructure operators. |
| Chapter 7, printed p. 69; PDF page 91 | Reveal information in proportion to an authorized purpose without imposing unnecessary obstacles on rightful access. |
| Chapter 9, printed p. 93; PDF page 115 | Access restrictions need oversight because authorized access can still be abused. |
| Chapter 10, printed p. 121; PDF page 143 | Removal from an interface or ordinary deletion does not establish irreversible destruction. |
| Chapter 10, printed p. 125; PDF page 147 | Retention and purging limit exposure and later repurposing of collected information. |

Page references use the supplied 200-page PDF, counting the first PDF page as
page 1. Printed page numbers are those shown in the book. These are paraphrases
of the selected sections, not an assessment of the entire book or a statement
of current legal requirements.

## Where the drafts need to be stronger

**Consent must follow purpose.** Local viewing, local outcome records, saved
text, remote analytics, and support uploads are different activities. One
choice must not authorize the others. Declining optional activities should
preserve core rewriting. Withdrawal should stop future optional recording and
transmission, including pending uploads; deletion of existing records needs a
separate, clear control. Those broader guarantees are proposed, not verified.

**Minimal analytics is still collection.** An aggregate can reveal information
about a small group or become identifying when combined with repeated reports
or transport metadata. Excluding prompts is necessary but not sufficient.
Approve the measurement's purpose, fields, recipients, and retention before
collecting anything. A central event history retained for later aggregation
would not meet the selected aggregate-only policy. Do not call measurements
anonymous without supporting evidence.

**Privacy must include the owner.** Making records obscure or hard to export
would undermine the goal. The protection objective concerns company staff,
vendors, and unauthorized readers. Routine privileged access should be limited
to a documented purpose, with proportionate oversight that does not copy
sensitive content into audit records. Necessary security records need their
own narrow purpose and bounded retention, not an exemption for analytics.

**Local is a location, not an encryption guarantee.** Filesystem permissions
reduce access but do not prevent the same user, administrators, or backups from
exposing plaintext. No policy statement should imply owner-only decryption or
end-to-end protection that the implementation has not established. Transient
processing, a TLS-terminating proxy, and the user's host application also see
information while performing their functions.

**Deletion claims need boundaries.** The seven-day trace and 30-day metrics
policies concern different local stores and require software execution. Managed
deletion does not control exports, recordings, backups, or provider records.
The revised policies should offer clear requests and controls without claiming
forensic erasure or the ability to extract an individual's contribution from
an aggregate after identifiers have been discarded.

## Findings at the inspected revision

| Proposed commitment | Current evidence or unresolved gap |
| --- | --- |
| Every optional persisted local record defaults off. | The inspected code retained text-free outcomes without full-text consent. The successor implementation gates all new outcomes under ADR 13; the `privacy_runtime` adoption check still requires evidence for the shipped revision. |
| Future remote analytics require separate opt-in and contain minimal aggregates only. | No remote product analytics feature was established. Review purpose, fields, recipients, retention, withdrawal, and disclosure risk before introduction. |
| Personal information is protected from unauthorized inspection. | Local traces remain unencrypted by Savior. Email, provider, and privileged-access practices require the `email_operations` and `retention_operations` checks; administrator resistance is not promised. |
| Owners can obtain and delete managed information. | Local export and deletion have documented limits. Company request procedures and support/backup deletion still require operational evidence. |
| Collection stays within the stated purpose. | Hosting and downloads expose network metadata. Necessary security records need their own inventory; analytics consent grants no advertising, sale, or training permission. |

The [review guide's evidence map](README.md#evidence-map) identifies the inspected
revisions and current behavior. This is a policy critique, not new runtime or
production verification. The revisions preserve current limitations rather
than replacing them with aspirational claims.

## Adoption result

The [company privacy policy](../../legal/website-privacy.md#collection-and-choice)
owns the shared commitments prepared for adoption. The
[Savior privacy notice](../../legal/savior-privacy.md) places
them beside the current product behavior and known gaps. Both terms documents
state that their acceptance is not analytics consent and preserve the software
license boundary. The [decision register](README.md#decisions-before-adoption)
keeps unverified company facts and adoption requirements open.

This critique is historical analysis. The agreement and consent changes are
separate implementation work; their publication and operational qualification
remain subject to the adoption register.
