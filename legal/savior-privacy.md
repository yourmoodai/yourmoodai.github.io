# Savior privacy notice

Savior processes supported prompt submissions on your computer. This notice
describes the development software's processing and optional local records.
The [company privacy policy](/legal/privacy/) covers website visits, support
messages, and the optional cloud receipt service.

## Local processing and its limits

Savior detects and replaces selected phrases locally, preserving unmatched text.
Inference runs offline after required artifacts are available. Its processing
path does not send prompts to a remote detector or import them into training.
Processing logs and errors exclude prompt content.

A supported submission continues only after successful processing. Failure
blocks that attempt without forwarding an original-text fallback. Detection
can miss wording or select text you wanted to preserve. Review changes before
relying on them.

The host application holds the original draft. Unloaded or disabled adapters,
unsupported origins, slash commands, host telemetry, voice features, other
extensions, terminal captures, and backups are outside this boundary. A reported
continuation is not proof of provider receipt. Your AI provider handles
submissions under its own policies.

## Temporary inspection

Host and terminal views can display edits locally without saving history. The
authenticated loopback browser inspector holds live activity in memory, bounded
to 1,000 observations and 32 MiB of serialized activity. Restarting clears the
live session; pausing the display does not stop temporary collection. Its browser
session storage contains an access token, not prompt text. Anyone viewing or
recording the display can create copies outside Savior's control.

## Consented history

History defaults off. Its separate opt-in covers all persisted trace events,
including text-free outcomes, errors, and cancellations. Declining or dismissing
the choice saves no new history and does not prevent rewriting or temporary
inspection. Revocation prevents earlier in-flight work from saving afterward.
Existing explicit consent remains valid for its existing purpose; it grants
no permission to upload records or collect remote analytics.

Consented records may contain original prompts, replacements, processing
details, and annotations. Records are profile-specific and protected by file
permissions but are not encrypted by Savior. Same-user processes, device
administrators, backups, or compromise can expose them. Local processing does
not imply owner-only decryption or resistance to administrators.

The trace store uses a seven-day retention policy and a 256 MiB ceiling.
Capacity reservations can evict records earlier. Expiry runs during capture
and inspection, not while the software is stopped. Disabling history stops new
records; existing records remain until expiry or deletion. Deletion also disables
capture and removes managed records, annotations, and projections. It does not
remove exports, screenshots, recordings, host history, provider records, or
backups, and does not promise forensic erasure.

## Separate personal review

Personal review requires its own enabling choice. Live review observes processed
input; history review separately reads bounded samples from the selected host
profile. Its local metrics store retains counts, proportions, confidence,
categorical judgments, and provenance hashes under a 30-day policy. It excludes
originals, replacements, excerpts, titles, and judge explanations. Expiry needs
software execution; exports remain user-managed. Personal-review consent does
not enable trace history or cloud uploads.

## Downloads, gateway, and company contact

Setup can request artifacts from GitHub Releases or model hosts such as Hugging
Face. Those services receive network metadata. No published Savior release is
established by this notice; development source is not an installed-release
qualification.

The explicit development gateway terminates HTTPS locally and can see decrypted
requests, including headers and prior history, while rewriting a supported user
field. It validates upstream TLS and does not log or persist prompts in that
path. Protect its local certificate authority key. The gateway does not prevent
direct connections; the macOS relay remains an unactivated candidate. No real
client has qualified gateway coverage or system-wide enforcement.

These local features do not automatically upload traces or metrics to Your Mood.
Support material must be deliberately submitted for its stated purpose. Remote
analytics, if introduced, require a separate choice and only minimal,
content-free aggregates. Neither an agreement acknowledgment nor a receipt
upload enables analytics. Contact
[support@yourmood.ai](mailto:support@yourmood.ai) for company-held information;
the company cannot recover device-only records it never received.

The version and effective date displayed with this notice identify its text.
A changed purpose requires fresh consent rather than silent expansion of an
earlier choice. Publication evidence is separate from product qualification.
