# E02 Wave 4 frozen production semantic-gap result

| Field | Result |
|---|---|
| Governance base | `f545c37157845f0bd402215719cb8c747b7fc21d` |
| Fixture SHA-256 | `7c5f3483307d3c1ea8409f3700cbdf416e173aded4b54275a65ef0eee48d7299` |
| Repeats | 10 |
| Deterministic | yes |
| Receipt fingerprint | `5e497efe8392f10a36895cbeacd1ee00525005a3140c6ebe9c498bd2d52af7ef` |
| Gap classifications | 5 `UNSUPPORTED`; 1 `NOT_EXERCISED`; 0 `SUPPORTED_OBSERVED`; 0 `CONTRADICTED_OBSERVED` |
| Expectation mismatches | 0 case; 0 boundary probe |
| Evaluation errors | 0 |
| Import origin | Verified to worker worktree; only relative module paths recorded |

## Scope and authorship

The expectation matrix was hand-authored against the known frozen production
codebase and committed before E02 execution. It was not a blind independent
suite. Each execution used a new disposable local Core store containing only
symbolic synthetic records. The runner did not connect to the operator Core,
use personal context or credentials, call a network, provider, or model, or
change production behavior.

The JSON execution-origin receipt binds the run to governance commit
`f545c37157845f0bd402215719cb8c747b7fc21d`, records
`import_origin_verified_to_worker_worktree: true`, and lists only these
repository-relative origins:

- `packages/allthecontext/src/allthecontext/__init__.py`
- `packages/allthecontext/src/allthecontext/core/service.py`
- `packages/allthecontext/src/allthecontext/models.py`
- `packages/allthecontext/src/allthecontext/storage.py`

No absolute user path is serialized. Receipts produced before this runtime
origin check existed were invalidated and are not evidence; the checked report
was regenerated from the worker worktree.

`UNSUPPORTED` is an observed absence receipt. It is not conformance, a passed
product capability, or an evaluator failure. `NOT_EXERCISED` means the exact
semantic could not be invoked through the public production API; adjacent
boundaries may still have run.

## Gap classifications

| ADR-047 gap | Classification | Decisive observation |
|---|---|---|
| Generic epistemic role distinct from kind | `UNSUPPORTED` | Candidate and search schemas reject role fields. Two records carrying the same role-shaped structured metadata but different kinds collapse to one result when kind is substituted for role. |
| Project **AND** domain applicability distinct from explicit scope filtering | `UNSUPPORTED` | Candidate/search schemas have no first-class domain predicate. A project-only scope query returns both same-project records across domains, and a wrong `current_project` does not hard-exclude a single otherwise relevant record. |
| Dependency lineage and invalidation | `UNSUPPORTED` | Candidate schema rejects dependency fields. A derived record containing dependency-shaped structured metadata remains at version 1 and retrievable after its source is corrected. |
| Eviction, decay, and procedure retirement | `UNSUPPORTED` | Explicit expiry prevents retrieval but leaves the canonical record present. Confidence remains `0.9` across searches, and decay/retirement fields are rejected. |
| Same identifier after terminal purge | `NOT_EXERCISED` | Candidate input rejects caller-selected record IDs, so exact same-ID reuse cannot be invoked. The adjacent control passes: replayed content receives a fresh ID while one opaque tombstone remains for the purged ID. |
| Procedure preconditions and transfer applicability | `UNSUPPORTED` | Procedure precondition and `applies_to` fields are rejected. Equivalent structured metadata is inert; a wrong `current_project` still returns the procedure, while an explicit mismatched scope excludes it. |

## Adversarial boundary probes

The 15 preregistered boundary probes deliberately separate exact semantics
from nearby behavior:

- `SUPPORTED_OBSERVED`: explicit expiry eligibility, explicit scope exclusion,
  and fresh-identity plus tombstone behavior after purge.
- `CONTRADICTED_OBSERVED`: kind-as-role, project-label-as-project-and-domain,
  `current_project`-as-hard-gate, structured dependency metadata, and
  structured procedure metadata substitutions.
- `UNSUPPORTED`: direct role, project/domain, dependency, decay, procedure
  retirement, and procedure-semantic schema fields.
- `NOT_EXERCISED`: caller-selected reuse of a purged stable record identifier.

These boundary classifications do not replace the six gap-level
classifications above.

## Limitations

- The tests cover isolated SQLite Core stores and public or stable Python
  paths at one frozen commit.
- Structured-value probes establish only that the tested keys are inert; they
  do not imply that production documents those keys.
- Expiry is an eligibility control, not evidence of eviction, decay, or
  procedure retirement.
- The run did not exercise Relay, export restore, an answer model, a protected
  action, an external system, or physical storage beyond each temporary Core
  directory.

The machine-readable receipt is
`bench/reports/memory_reliability_e02_wave4.json`. The separate design receipt
is `docs/research/ATC_MEMORY_LAB_E02_WAVE4_IMPLEMENTATION_BOUNDARY_2026-07-23.md`.
