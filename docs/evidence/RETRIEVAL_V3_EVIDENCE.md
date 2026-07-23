# Retrieval V3 evidence

Evidence date: 2026-07-22. The comparator is the frozen
`codex/core-only-v1` commit `70a4808cc5d9fc35f4a7b9a75bc3cbfbb0e9ce40`.
All commands ran from the isolated `codex/retrieval-v3-integration` worktree
with `PYTHONPATH` resolved to that worktree. The separate
`codex/provider-archive-ingestion` worktree was not edited, reset, rebased, or
merged.

## Integrated commits

| Stage | Implementor commit | Integrated commit |
|---|---|---|
| Contracts/comparator/fixtures | `db3b713206013f1665c98b4bbae4c603704d8934` | `c40d2e6576b0dd79d88453d613b4bb45d875a46d` |
| Candidate-scoped lexical V3 | `d06eb72d321261e426176b8f0300f35a2a91b50c` | `d89ea5cfd8be44f5ec3c303a24c6e3ec32740f70` |
| UTC temporal sidecar | `61eab1786f3a85f616c67637acf94d5aff92a045` | `b3eed9f6d10658bb9bf31f590f65a32da483c727` |
| Task admissibility | `4d1d9c66a1a70c1de0f3d2a51cf54bbb6b87e189` | `97a311760c32de6be897fa56407268b4eb27fe50` |
| Wave 1 coordinator wiring | - | `659c79136b5d5ba66b9cc5e38640a9d3f341cff3` |
| Set-selection module/fixtures | `d3a25da8f3d99de1ad8684d38658acba213823bf` | `dd38237a30e54430347ccd9906d4c5d56f64f12f` |
| Production compiler wiring | - | `f6ce83d670416375066276d4947168597b55387e` |
| Optional dense shadow | `4098cf4c1a5445f978bb45edee980d965f816b82` | `2539fabb0062df46ba0720066b4741a13bce9683` |
| Source-evidence research | `16418d02db3f4f5f74ebc72c41e2252af2a27cb8` | `762bb42563d1e17ed5e62a9b8877e642617d129c` |

Feature tasks changed focused new modules, fixtures, reports, and tests. The
coordinator owned `retrieval.py`, request/MCP/CLI forwarding, integration tests,
and the material architecture/status/decision/traceability updates.

## Final local verification

Environment: Windows, Python `3.12.10`. Exact commands and results:

```text
$env:PYTHONPATH=(Resolve-Path 'packages/allthecontext/src').Path
py -3.12 -m ruff format --check .
# 158 files already formatted.

py -3.12 -m ruff check .
# All checks passed.

py -3.12 -m mypy packages/allthecontext/src
# Success: no issues found in 57 source files.

py -3.12 -m pytest
# 433 passed, 4 skipped, 3 dependency-deprecation warnings in 73.65s.
# All four skips require symlink creation unavailable to this Windows account.

py -3.12 scripts/check_docs.py
# Exit 0.

cd apps/dashboard
npm run check
npm test
npm run build
npm audit --audit-level=high
# Type check passed; 17 tests passed; production build passed; 0 vulnerabilities.
```

## Retrieval acceptance evidence

```text
py -3.12 -m bench.retrieval_v3_combined --output tmp/retrieval-v3-post-format-py312.json
```

The combined report passed every enforced gate at 1k and 10k:

- exact Recall@5 `1.0`, equal to the frozen comparator;
- admissibility precision `1.0` versus comparator `0.5`;
- temporal precision `1.0` versus comparator `0.5`;
- semantic coverage `1.0`, equal to comparator;
- duplicate redundancy `0.0`, policy violations `0`, and deterministic repeated
  rankings/conflict behavior;
- deleted/purged resurrection count `0`, with current/`as_of`, migration,
  restart, portable restore, and stale-sidecar rebuild scenarios passed;
- 10k warm p95 `89.94938 ms`, below the `150 ms` gate; and
- combined database/sidecar growth `1027.185778` bytes per added record from 1k
  to 10k.

```text
py -3.12 -m bench.set_selection_benchmark --output tmp/set-selection-final-py312.json
```

All 11 set gates passed: exact expected selection, mandatory preferences,
semantic coverage `1.0` versus baseline `1.0`, deterministic repeated/reordered
inputs, hard upstream attestations, budgets, support relationships, and zero
duplicate/conflict/compatibility violations.

## Optional research evidence

```text
py -3.12 -m bench.dense_shadow_benchmark --exact-scan-only --profiles 128 1024 10000 --include-10k --output tmp/dense-shadow-final-py312.json
```

The synthetic non-semantic 384d float32 exact scan was deterministic and used
exactly `1536` vector bytes per candidate (`15,360,000` at 10k). Its 10k warm
p95 was `400.294955 ms`, missing the explicit `150 ms` target. The real optional
local model and semantic comparison were `not_exercised`; ANN is unimplemented
and has no production authority. The latency miss permits a later bounded ANN
shadow study, but not promotion before semantic benefit and recall are measured.

```text
py -3.12 -m bench.source_evidence_retrieval --output bench/reports/source_evidence_retrieval_wave2.json --markdown bench/reports/source_evidence_retrieval_wave2.md
```

The 64/256-source profiles passed with evidence recall `1.0`, facet coverage
`1.0`, deterministic repeats, ineligible-corpus invariance, and zero policy
violations. At 256 sources, diversity-aware token MaxSim reduced redundancy
from the lexical source pool's `0.083334` to `0.0` at `18.9572 ms` warm p95.
Neural/model late interaction remains `not_exercised`, and no variant is wired
into the application.

## Hosted cross-platform evidence

Pending the final branch push. The required workflow is `.github/workflows/ci.yml`:
Python 3.12 on Windows, macOS, and Ubuntu; dashboard Node 20/22; and native
desktop/package jobs on Windows, Ubuntu, macOS ARM, and macOS Intel. This section
must be updated with the exact run and result before the work is called complete.

## Remaining risks and unexercised claims

- Set selection is deterministic marginal greedy selection, not a globally
  optimal knapsack solver. Its sanitized fixture has 21 candidates; the 10k
  hard cap is not a performance claim.
- The dense synthetic runtime measures mechanics only. A genuine local model,
  semantic coverage, cross-platform vector determinism, packaging audit, and
  ANN recall/latency are unexercised.
- Source-evidence fixtures are sanitized and synthetic. Neural late interaction,
  representative imported archives, and the opt-in 1024-source profile are
  unexercised.
- An in-place same-ID correction exposes the latest canonical content across its
  validity interval; earlier same-ID content remains an audit-history concern.
  Separate superseding records provide historically searchable content.
- Learned sparse retrieval, a production reranker, production late interaction,
  and production vector/ANN retrieval were not scheduled because their promotion
  evidence is absent. No paid/hosted service or default native dependency was
  added.
