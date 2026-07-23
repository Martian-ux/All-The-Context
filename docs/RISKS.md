# Risks and mitigations

| Risk | V1 mitigation |
|---|---|
| Direct mobile access tempts unsafe public exposure | Keep Core on `127.0.0.1`; never open a port automatically; withhold one-click mobile claims until pairing, TLS, revocation, and recovery pass |
| Automatic maintenance applies a false or stale observation | Let only Core decide; use server-derived origin, explicitness, versioned deterministic policy, memory-slot conflict rules, provenance, immutable history, optional activity inspection, and reversible correction/forget/restore |
| A model inference masquerades as a user fact | Authenticated transport does not grant disposition control; inference remains tentative unless eligible explicit evidence corroborates it; an authorized malicious client remains a documented residual risk |
| The absence of routine review hides a policy defect | Record `policy_version`, decision reason, origin, timestamps, and evidence links; expose optional activity and undo; make migrations idempotent; test decision matrices and replay determinism |
| A client reads records outside its authority | Authenticate scoped client identities and apply permissions, applied disposition, validity, deletion, and supersession before every retrieval channel |
| A managed client attaches to the wrong Core | Bind generated configuration to the exact data directory, instance proof, port, client ID, and credential |
| Large or malicious imports exhaust resources | Chunk HTTP/raw SQLite writes, stream top-level conversation arrays, enforce entry/raw/expanded-size/compression bounds, and use resumable batches |
| Provider archive prose or assistant output injects false context | Treat all imported text as inert data; normalize and exclude assistant/system/tool/attachment roles; keep generic instruction-bearing text and provider-synthesized memory tentative; publish no current change before successful extraction |
| Provider role labels are wrong | Preserve byte-exact source and message provenance, use conservative parser-specific origin rules, retain bounded policy reasons, and make ordinary automatic changes reversible |
| Undocumented provider schema changes silently omit history | Preserve the complete raw archive, publish detected counts/warnings/limitations, fail coverage closed on parse errors, and version parsers for later reprocessing |
| Interrupted migration, policy evaluation, export, shutdown, or update corrupts state | Use SQLite transactions/backups, portable locks, idempotency keys, journals, health checks, and tested rollback paths |
| Credentials or raw personal context leak through logs, browser state, or configuration | Use OS credential abstractions, opaque one-use browser sessions, redaction, no raw-context logging, and least-privilege tokens |
| Unsigned community packages are mistaken for publisher-signed software | Prominent unsigned labels plus checksums, SBOM/provenance, immutable assets, and offline Ed25519 update manifests |
| Cross-platform claims exceed evidence | Keep Windows/macOS/Linux CI and distinguish authored from observed acceptance |
| Live SQLite is readable to the OS account | Document the boundary; rely on account/disk protection; encrypt portable exports |
| Dormant experimental Edge code makes network calls or creates current context | Do not start its worker; remove deployment UI/workflows/templates; permit Relay to queue observations and accept signed Core projections only; retain explicit compatibility/cleanup paths until deletion is safe |
| Legacy `always_available` records imply an offline guarantee | Label them legacy, do not offer the value for new current context, and let users migrate them to `core_available` |
