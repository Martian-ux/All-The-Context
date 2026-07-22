# Active risks

| Risk | Response |
|---|---|
| MCP Python v2 is imminent | Pin stable v1 below 2; isolate adapter; add contract test |
| Relay SQLite differs from hosted PostgreSQL | Keep SQL/storage boundary explicit; do not claim PostgreSQL exercised |
| OS credential backends differ | Use `keyring` abstraction and test file fallback; platform stores need real-OS validation |
| Engineering desktop artifacts are unsigned | Keep claims explicit; add Windows signing, macOS notarization, and publisher identity before public release; retain packaged uninstaller smoke coverage |
| Large/malicious imports | Enforce byte/record/archive limits and keep source text inert |
| Deletion/permission replication lag | Durable outbox, checkpoints, status visibility, reconciliation tests |
| Full provider capability changes over time | Official-source capability matrix with verification date and unverified labels |
| A public Edge forwarding queue could amplify requests, forge client authority, replay results, or retain private Core data | Core initiates outbound polling; seal requests to Core before SQLite; resolve a Core-local user approval instead of Edge assertions; enforce one-use expiring claims, replay/cancel checks, per-client/global bounds, response limits, sanitized errors, and memory-only responses; byte-scan DB/WAL/SHM sentinels in security tests |
| Local SQLite content is plaintext to the OS account | Document the boundary; rely on OS disk/account protection until an application-encrypted vault is designed |
| Cross-platform claims exceed local hardware | Keep CI for all three operating systems and withhold observed-support claims until those jobs run |
| A reused venv retains compiled modules from another Python version | Bootstrap compares runtime versions and probes compiled imports before reuse; rebuild stale environments |
| OS keyring silently discards a credential | Read back and compare every setup write; fall back with an explicit warning rather than declaring setup complete |
| Frozen STDIO wrappers close real process handles | Own and detach UTF-8 wrappers explicitly; reject any packaged MCP stderr traceback in the first-run smoke |
| Browser setup links expose an administrator credential, another loopback service impersonates Core, or another origin submits an authenticated mutation | Verify a per-installation challenge proof before sending the administrator credential; exchange a random one-use 60-second ticket for an opaque tab session backed only by Core memory; require a custom dashboard header on mutations |
| A running AI client locks the packaged MCP executable during upgrade | Install a content-addressed replacement and repair existing managed client configurations to select it on the next launch |
| A frozen Windows Core stops listening but a stale connection/task delays process teardown | Allow a five-second graceful drain, then let Uvicorn cancel remaining tasks; packaged smoke requires process exit and unlocked cleanup |
| Release infrastructure is mistaken for an implemented updater | Keep OTA work limited to a strict signed contract and operator tooling; implement download/install/rollback only in a separately reviewed branch |
| A release key is exposed to GitHub or a build worker | Keep private keys offline and outside the repository; store only reviewed public keys; use overlapping rotation and explicit revocation |
| A mutable URL serves different update bytes | Require version in the HTTPS path, reject `main`/`latest` and queries, sign digest and size, and never replace a signed release asset |
| GHCR metadata or a hosted template is treated as production validation | Pin deployed images by digest; verify anonymous access and provider flows separately; do not claim hosting or mobile acceptance from authored configuration |
