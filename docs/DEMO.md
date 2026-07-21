# Reproducible vertical-slice demonstration

The demonstration exercises the first All The Context vertical slice against
real Core, Relay, replication, archive-import, synchronization, and encrypted
export services. All data is fictional. The script prints only boolean and
count evidence; it does not print credentials or raw context.

After installing the project, run this command from the repository root in
PowerShell, macOS Terminal, or a Linux shell:

```text
python scripts/demo.py
```

By default, the demo uses an automatically removed temporary directory. To
retain the SQLite databases and encrypted export for inspection, pass a fresh
directory using syntax supported by every platform:

```text
python scripts/demo.py --workspace demo-output
```

The script refuses to reuse its named Core, Relay, restore, or export targets;
it does not delete or overwrite an earlier run.

## What the demonstration proves

The script fails immediately if any claimed invariant is false. A successful
run reports `"result": "passed"` and evidence for these operations:

1. Initialize an authoritative Core and independent Relay database.
2. Import a fictional malicious-instruction archive as inert raw source data.
   Its candidate remains pending, and a secret-like labeled fact is excluded.
3. Start model-assisted ingestion with explicit available and unavailable
   coverage, submit a preference and project decision, replay the batch to
   prove idempotency, finish the coverage report, and approve both candidates.
4. Sign and replicate only the approved `always_available` preference.
5. Release the Core service object, then retrieve the preference from Relay.
   Searching Relay for the `core_available` decision returns a clean empty
   result, which is the expected reduced/offline behavior.
6. Queue a noncanonical proposal at Relay while Core is unavailable.
7. Reopen Core from the same database, retrieve the Core-only decision, import
   and acknowledge the Relay proposal, and leave it pending for review.
8. Correct the replicated preference and verify its new hash at Relay; delete
   it and verify the tombstone removes it from Relay retrieval.
9. Revoke a Core client and verify its credential no longer authenticates.
10. Create an AES-GCM encrypted portable export, restore it into a freshly
    migrated database, and verify a canonical record by stable ID and hash.

The demo is an in-process, deterministic integration run: `TestClient` carries
HTTP requests to the Relay without opening a network port. Production Relay
deployment still requires HTTPS termination. Process startup and operating
system smoke coverage belong to the platform CI matrix.

## Automated coverage

`tests/e2e/test_demo.py` executes the complete script as a test. The focused
security tests additionally cover SQL/FTS injection handling, untrusted import
behavior, authentication and client isolation, signed-event tampering,
idempotent replay, out-of-order rejection, revocation, and deletion visibility.

Run the focused proof suite with:

```text
python -m pytest tests/e2e tests/security
```
