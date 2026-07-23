# Reproducible V1 demonstration

Run `scripts/demo.py` to exercise the authoritative single-Core path without a
network service, Docker, provider account, or personal data.

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

It proves:

1. Core initializes an authoritative per-user-style vault.
2. A malicious-looking Markdown archive is retained as inert source material
   and produces only pending candidates.
3. A model-assisted ingestion session declares available/unavailable coverage;
   an exact batch retry is idempotent.
4. A preference and project decision are approved as `core_available` and
   retrieved directly from Core.
5. Core is released and reopened from the same database; retrieval still works.
6. Correction advances history, deletion creates a tombstone, and a revoked
   client can no longer authenticate.
7. A complete encrypted export restores into a fresh Core and preserves the
   surviving record hash.

The demonstration intentionally does not start the dormant experimental Relay
or claim access while Core is offline. Mobile follows the same direct-Core
authority and therefore requires Core to be online.
