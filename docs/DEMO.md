# Reproducible V1 demonstration

Run `scripts/demo.py` to exercise the authoritative single-Core path without a
network service, Docker, provider account, personal data, or memory review
step.

PowerShell:

```text
.\.venv\Scripts\python.exe scripts\demo.py
```

macOS or Linux:

```text
./.venv/bin/python scripts/demo.py
```

The script uses a temporary directory unless `--workspace` names a fresh one.
It refuses to reuse Core, restore, or export targets and prints only
non-sensitive evidence.

The automatic-context demonstration proves:

1. Core initializes an authoritative per-user-style vault.
2. A malicious-looking Markdown archive is retained as inert source material;
   extracted observations remain noncurrent and a secret-like fact is not
   extracted into context.
3. A model-assisted ingestion session declares available/unavailable coverage,
   and an exact observation-batch retry is idempotent.
4. On successful finish, an explicit user preference and project decision are
   applied automatically as `core_available` and retrieved directly from Core
   without an approval call or dashboard visit.
5. Core is released and reopened from the same database; retrieval still works.
6. Correction advances history, ordinary deletion creates a reversible
   tombstone, and a revoked client can no longer authenticate.
7. A complete encrypted export restores into a fresh Core and preserves the
   surviving current-record hash.

The script is release evidence only when it completes these steps without a
record-approval call and its output contains no private content. The separate
automatic-policy unit/integration gates additionally cover staged isolation,
duplicate reinforcement, tentative corroboration, same-slot precedence,
secret/sensitivity policy, deletion undo, and historical-version restoration.

The demonstration does not exercise the current pre-beta Edge/Relay
compatibility surface or claim access while Core is offline. Passing the demo
is therefore not evidence that B-103's removal and isolation gate has passed.
If the narrow compatibility path is exercised separately, Relay may queue
observations and accept signed Core projections only; it cannot create current
context. The beta has no supported mobile journey. Any future mobile or remote
client requires separate pairing, security, and release evidence.
