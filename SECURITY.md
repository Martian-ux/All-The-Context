# Security policy

Do not open public issues containing personal context, credentials, database
files, exports, or replication secrets. Report suspected vulnerabilities
through the repository's
[private vulnerability reporting form](https://github.com/Martian-ux/All-The-Context/security/advisories/new),
which was enabled and verified on 2026-07-25. If the form is unavailable,
contact the repository owner without putting sensitive details in a public
issue.

Public issues may carry only sanitized, content-free summaries (impact class,
gate ID, versions, digests). Keep exploit steps, credentials, tokens, private
keys, raw conversations, and exports in the private reporting path. Emergency
handling, sole-maintainer residuals, and operator repository-control checklists
are in
[docs/operations/REPOSITORY_SECURITY.md](docs/operations/REPOSITORY_SECURITY.md).

Core is loopback-only by default. V1 has no supported hosted Relay/Edge or
automatic remote-exposure path. Do not bind Core to a public interface without
an independently reviewed encrypted transport and authentication boundary.
The supported Core product does not construct or start the legacy Edge worker.
Ordinary Edge/Relay product API and CLI operation paths are removed or
tombstoned with HTTP 404 responses. Local-only legacy cleanup remains isolated
under explicit administrator surfaces and cannot create a second authority by
default. Exact downloaded-artifact isolation evidence remains required before
publication; source implementation alone is not a platform claim.

Clients use scoped credentials. Normal setup writes to the operating-system
credential backend and verifies the round trip. If protected storage is
unavailable or fails, setup stops, revokes newly created access, and restores
prior configuration; it does not silently select a plaintext fallback. The
plaintext file store is development-only and requires deliberate environment
opt-in. Exact-package Windows, macOS, and Linux protected-store receipts remain
release gates and are not implied by this source contract.

An ATC-configured same-device Codex or Claude principal may receive the
explicit-statement witness grant. That grant lets the client attest that
specific text was directly stated by the user; it is not cryptographic proof
of authorship, is not implied by authentication alone, and does not apply to
model inference or imported history. A malicious authorized client can still
lie, so that residual must remain visible in the threat model and beta
limitations.

Secret-like direct content must be refused or irreversibly redacted before its
payload reaches durable state. A refused value must not leave an unkeyed
content hash or other guessable verifier. Existing-data repair must cover live
SQLite pages/freelists, WAL/journal/SHM, FTS, temporary state, diagnostics, and
new exports; external historical backups and device remanence require explicit
operator retirement/warning rather than a false erasure claim.

The repository threat model is
[docs/security/All The Context-threat-model.md](docs/security/All%20The%20Context-threat-model.md).
