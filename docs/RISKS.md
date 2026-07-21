# Active risks

| Risk | Response |
|---|---|
| MCP Python v2 is imminent | Pin stable v1 below 2; isolate adapter; add contract test |
| Relay SQLite differs from hosted PostgreSQL | Keep SQL/storage boundary explicit; do not claim PostgreSQL exercised |
| OS credential backends differ | Use `keyring` abstraction and test file fallback; platform stores need real-OS validation |
| Engineering desktop artifacts are unsigned | Keep claims explicit; add Windows signing, macOS notarization, publisher identity, and a standard uninstaller before public release |
| Large/malicious imports | Enforce byte/record/archive limits and keep source text inert |
| Deletion/permission replication lag | Durable outbox, checkpoints, status visibility, reconciliation tests |
| Full provider capability changes over time | Official-source capability matrix with verification date and unverified labels |
| Hosted Relay cannot reach online Core for deep retrieval yet | Return a clean reduced result for `core_available`; add an authenticated outbound Core channel later |
| Local SQLite content is plaintext to the OS account | Document the boundary; rely on OS disk/account protection until an application-encrypted vault is designed |
| Cross-platform claims exceed local hardware | Keep CI for all three operating systems and withhold observed-support claims until those jobs run |
| A reused venv retains compiled modules from another Python version | Bootstrap compares runtime versions and probes compiled imports before reuse; rebuild stale environments |
| OS keyring silently discards a credential | Read back and compare every setup write; fall back with an explicit warning rather than declaring setup complete |
| Frozen STDIO wrappers close real process handles | Own and detach UTF-8 wrappers explicitly; reject any packaged MCP stderr traceback in the first-run smoke |
