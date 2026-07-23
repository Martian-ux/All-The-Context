# Risks and mitigations

| Risk | V1 mitigation |
|---|---|
| Direct mobile access tempts unsafe public exposure | Keep Core on `127.0.0.1`; never open a port automatically; withhold one-click mobile claims until pairing, TLS, revocation, and recovery pass |
| A model writes false or malicious durable memory | Store proposals as candidates; keep source text inert; require review/policy before canonical approval |
| A client reads records outside its authority | Authenticate scoped client identities and apply permissions/validity/deletion before every retrieval channel |
| A managed client attaches to the wrong Core | Bind generated configuration to the exact data directory, instance proof, port, client ID, and credential |
| Large or malicious imports exhaust resources | Enforce byte/record/archive bounds, idempotency keys, and resumable batches |
| Interrupted migration, export, shutdown, or update corrupts state | Use SQLite transactions/backups, portable locks, journals, health checks, and tested rollback paths |
| Credentials leak through logs, browser state, or configuration | Use OS credential abstractions, opaque one-use browser sessions, redaction, no raw-context logging, and least-privilege tokens |
| Unsigned community packages are mistaken for publisher-signed software | Prominent unsigned labels plus checksums, SBOM/provenance, immutable assets, and offline Ed25519 update manifests |
| Cross-platform claims exceed evidence | Keep Windows/macOS/Linux CI and distinguish authored from observed acceptance |
| Live SQLite is readable to the OS account | Document the boundary; rely on account/disk protection; encrypt portable exports |
| Dormant experimental Edge code makes network calls | Do not start its worker; remove deployment UI/workflows/templates; retain only explicit compatibility/cleanup paths until deletion is safe |
| Legacy `always_available` records imply an offline guarantee | Label them legacy, do not offer the value for new approvals, and let users migrate them to `core_available` |
