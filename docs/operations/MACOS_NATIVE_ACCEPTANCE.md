# Native macOS Beta 1 acceptance

macOS remains a mandatory Beta 1 platform on both native ARM64 and native
x86-64. Deferring a physical-machine run does not pass, waive, skip, or narrow
either target. Rosetta on Apple Silicon is not Intel acceptance.

This runbook separates preparation that can be automated before hardware is
available from acceptance that must be observed on the two real Macs. A green
hosted job or preparation report grants zero native acceptance credit and must
not be converted into a canonical gate receipt.

## Frozen host and input contract

Use a dedicated clean local user on each Mac. Before the first product launch,
freeze and retain only these content-free facts:

- native architecture: `arm64` or `x86_64`, with Rosetta translation absent;
- exact macOS 26 product version and build;
- at least four logical CPUs and 8 GiB physical memory;
- internal solid-state root storage with strictly more than 16 GiB free;
- exact stable Codex and Claude Desktop versions for that architecture;
- exact 40-character candidate source commit, 64-character candidate SHA-256,
  version, draft release identity, and DMG/ZIP names, sizes, and SHA-256 values.

The exact version and build are frozen once both physical machines are chosen.
Do not silently substitute a later patch, beta OS, VM, compatibility layer, or
different client build. Run as the dedicated non-root user with an unlocked
login Keychain. Do not disable Gatekeeper, remove quarantine attributes, add a
publisher signature, or modify the candidate bytes.

The full candidate asset directory is required, not only the two Mac files.
The candidate verifier recomputes all 52 declared artifact descriptors plus
the source-evidence relationships and checksum sidecars. Keep that directory
read-only during a run. The results directory must be new, outside every
source checkout, and outside synchronized storage if the evidence contains
machine-specific operational metadata.

## Exact source environment

Use a clean detached checkout at the candidate's recorded source commit. Build
one Python 3.12 environment from that checkout's reviewed lock and install the
exact checkout into it:

```sh
python3.12 -m venv .venv
.venv/bin/python scripts/install_locked_python.py --extra packaging
git status --short
```

The final command must print nothing. The pinned `uv` version required by the
lock installer must already be present; the installer does not network-bootstrap
unreviewed tooling.

The preparation runner may come from a separate reviewed tooling checkout when
testing an older frozen candidate that predates it. Invoke the runner with the
candidate checkout's `.venv/bin/python`, not the tooling checkout's Python.
The runner refuses an import path that does not resolve to the exact candidate
checkout and records SHA-256 identities for its four supporting tool files.
For a future candidate that contains the runner, the tooling and candidate
checkouts may be the same directory.

## Cheap host refusal check

Run this before downloading or copying large assets. Replace the version and
architecture with the exact frozen values for that host:

```sh
.venv/bin/python scripts/macos_acceptance_preflight.py \
  --profile native-acceptance \
  --expected-architecture arm64 \
  --expected-major 26 \
  --expected-os-version 26.0 \
  --dedicated-clean-user-attested \
  --output /absolute/new/results/arm64-preflight.json
```

The preflight records only allowlisted host facts. It fails for the wrong OS,
architecture, Rosetta, patch, resources, storage, root execution, missing
native tools, or missing clean-user attestation. It performs no product
journey and always records `acceptance_claimed=false`.

## Candidate-bound supporting checks

After the full asset set is present, run the architecture-specific preparation
suite. The results directory must not already exist:

```sh
CANDIDATE_PYTHON=/absolute/candidate-checkout/.venv/bin/python
TOOL_ROOT=/absolute/reviewed-tooling-checkout
CANDIDATE_ROOT=/absolute/candidate-checkout
ASSETS=/absolute/full-candidate-asset-directory

"$CANDIDATE_PYTHON" \
  "$TOOL_ROOT/scripts/run_macos_native_supporting_checks.py" \
  --project-root "$CANDIDATE_ROOT" \
  --release-dir "$ASSETS" \
  --candidate "$ASSETS/release-candidate-v1.json" \
  --expected-candidate-sha256 CANDIDATE_SHA256 \
  --architecture arm64 \
  --expected-os-version 26.0 \
  --results-dir /absolute/new/results/arm64-supporting \
  --dedicated-clean-user-attested
```

Repeat separately with `x86_64` on the native Intel Mac. The runner:

1. verifies the exact candidate inventory and exact clean source commit;
2. verifies the interpreter imports the candidate checkout, not another clone;
3. executes the strict native host preflight;
4. verifies the architecture-specific direct package report, checksum,
   unsigned disclosure, DMG integrity, app identity, internal links, structural
   code seal, and the architecture of the app, MCP helper, and recovery helper;
5. mounts the DMG read-only at a unique temporary mount point, copies the app
   with `ditto`, detaches it, and re-verifies the staged bundle;
6. runs isolated real-Keychain adapter checks, frozen-resource diagnostics,
   packaged recovery smoke, and isolated packaged first-run/MCP/restart smoke;
7. deletes only its exact staging and temporary roots and verifies the source
   checkout is still clean; and
8. retains only the preflight plus a content-free phase/status/byte-count
   report. Raw subprocess streams, tokens, vault content, and client content
   are not retained.

Even when every supporting phase passes, the report says
`preparation_only=true`, `acceptance_claimed=false`, and
`canonical_receipts_emitted=false`.

## Supervised native acceptance window

Use only fresh fictional data and new client chats. Never open or capture an
existing conversation, provider export, normal vault, unrelated Keychain item,
or unrelated client configuration. Snapshot the exact Codex/Claude config
bytes and metadata before each run, restore any run-attributed change, and
verify equality afterward. `BETA-P04` provider-export acquisition is already a
separate requirement; this Mac window must not request, download, or generate
another provider export.

Run these three subjourneys once per native architecture against the same
authenticated candidate bytes. Stop on the first product failure and perform
cleanup before retrying.

### 1. Install, protected credentials, and real clients

This supplies supporting slices for `BETA-P01`, `BETA-S03`, `BETA-P02`,
`BETA-P03`, and `BETA-S02`:

- obtain the DMG through the real controlled download path so macOS applies
  normal quarantine metadata;
- open it through the normal user path, observe the unsigned-community warning,
  and use only Apple's ordinary user override; never run `xattr -d`, disable
  Gatekeeper, or use `spctl --master-disable`;
- verify install into `~/Applications/All The Context.app`, no root request,
  one loopback Core, the stable bundle path, and a per-user LaunchAgent;
- verify intended Keychain entries are created without a token in managed
  client configuration, and inject bounded storage/config failures to prove
  rollback leaves no new principal, credential, backup, or partial config;
- in the current stable native Codex, use a new fictional chat to configure
  once, apply a direct statement, close Codex and Core, relaunch a fresh chat,
  and retrieve the same record without review or dashboard work;
- submit an exact replay and duplicate and prove idempotent reinforcement; then
  submit a conflicting model-inferred fictional value and prove the explicit
  current record remains unchanged while the conflict is tentative;
- repeat the same sequence in the current stable native Claude Desktop; and
- log out/in and reboot once to prove the LaunchAgent and both managed clients
  reconnect to the same vault without reinstalling.

Record only IDs, counts, dispositions, hashes, versions, booleans, timings, and
closed reason codes. Do not retain prompt or response text.

### 2. Security, deletion, and packaged recovery

This supplies supporting slices for `BETA-S01`, `BETA-S04`, `BETA-D02`, and
`BETA-D03`:

- submit fixed fictional secret-like canaries and a content-derived verifier;
  prove refusal before durable payload/hash writes, content-free replay and
  forget reasons, and zero canary hits in SQLite tables/pages/freelist, FTS,
  live WAL, diagnostics, and encrypted exports after repair/compaction;
- prove the supported package has no active Edge/remote operation route,
  non-loopback listener, pairing, deployment, or remote cleanup path; the
  isolated legacy cleanup endpoint must refuse when no residual state exists;
- exercise scoped-reader allow/deny/revocation, ordinary record and source
  delete/restore, wrong-confirmation and scoped-client purge refusal, deliberate
  administrator purge, restart, encrypted export, isolated restore, and
  non-resurrection; and
- with Core stopped, use only the bundled version-matched
  `all-the-context-recovery` helper to prove help/doctor, encrypted export,
  wrong/missing passphrase, missing/tampered input, nonempty destination,
  isolated restore with non-vacuous retrieval, cutover, failed cutover
  preservation, rollback, and tampered/missing rollback refusal.

Run `PRAGMA quick_check` and `PRAGMA foreign_key_check` only on the disposable
vault. Never scan unrelated files or credentials.

### 3. Allocated two-billion-byte boundary

This supplies the native `BETA-D01` slice:

- predeclare the host, APFS volume, resource budgets, candidate identities, and
  output locations before allocating data;
- create the deterministic `boundary-canary-v2` at exactly 2,000,000,000 bytes,
  `fsync` it, prove its SHA-256, and prove it is physically allocated and not a
  sparse/compressed placeholder using both logical size and allocated blocks;
- prove a declared 2,000,000,001-byte operation is refused before upload;
- run straight import and exact repeat; require one source identity, 239 source
  chunks, five candidates, closed reconciliation, stable candidate IDs, and
  non-vacuous retrieval;
- require progress start within five seconds and continued movement within five
  seconds or 64 MiB from both authenticated API and read-only SQLite observers;
- require Core plus import-worker peak RSS at or below 1 GiB and incremental
  storage at or below four times raw size plus 1 GiB;
- cancel after the full source is durably preserved, require durable
  acknowledgement within five seconds and worker quiescence within 30 seconds,
  then complete a no-upload retry without identity change;
- hard-kill only the run-owned packaged process group during parsing, restart,
  require the interrupted operation to fail with the closed restart code, and
  complete another no-upload retry; and
- export with source chunks, restore into isolation, and prove source SHA,
  chunk count/bytes, schema, record/candidate IDs, counts, and retrieval match.

Do not use APFS clones, `truncate`, `mkfile -n`, sparse seeks, compressed files,
or synthetic disk accounting. Remove the canary only after every owned process
and file handle is closed and the content-free report is durable.

## Evidence consolidation and cleanup

Use a separate copy of
[`MACOS_NATIVE_OBSERVATION_FORM.md`](MACOS_NATIVE_OBSERVATION_FORM.md) for each
architecture. The form is a content-free worksheet, not a receipt.

ARM64 and Intel observations remain separate supporting slices. The same is
true for Codex and Claude client cells. A platform/client slice must never be
placed directly into the final bundle as a duplicate gate ID. Only after every
frozen cell passes may the coordinator consolidate one unique candidate-bound
receipt per gate and eventually one `BETA-X01` receipt. Deferred, unavailable,
or unrun is not pass.

For every attempt, verify all of the following before leaving the dedicated
user account:

- Core is shut down through its authenticated endpoint when possible;
- zero run-owned app/helper processes and zero listeners on the selected ports;
- every run-owned Keychain item and principal is revoked and absent, without
  listing or reading unrelated items;
- client configuration matches its exact pre-run bytes and metadata;
- the run-owned LaunchAgent, installed test app, backups, temporary vault,
  canary, exports, restores, logs, mounts, and shortcuts are absent;
- the candidate asset directory is unchanged; and
- only content-free reports and validated supporting receipts remain.

Physical Mac execution is the point where product evidence begins. Until both
architectures and all four stable Mac client cells finish these journeys, Mac
support and `BETA-X01` remain open and the beta remains unpublished.
