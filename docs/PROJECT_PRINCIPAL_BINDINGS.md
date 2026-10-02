# Core project identities and principal bindings

This isolated local candidate starts at delivery-status revision
`97e0dbf3dd1b81dc33d464efd09ba47d6ad9ff0b`. It adds an opt-in routing boundary
for already registered narrow principals. It does not activate bindings in an
installed application or change the separate preference-collision candidate.

## Authority and lifecycle

An authenticated administrator selects an existing, explicit, current,
unconflicted project anchor with exactly one `project:` scope through
`POST /v1/admin/project-identities`. Core issues a canonical opaque project key.
The key is stored, rather than derived from project labels, filesystem paths,
capsule membership or query text. Two anchors with the same display label can
have different keys when their existing canonical record slots are distinct.
This does not repair the existing formation rules that can coalesce proposals.

The administrator binds a registered principal using
`POST /v1/admin/project-bindings`. Supported grants are exactly
`context:capture`, or `context:read` with optional `context:status`. Administrator,
wildcard and mixed read/capture principals cannot be bound. A capture principal
with an existing capture source must be replaced by a fresh principal; earlier
events cannot be reinterpreted under another project. These are setup APIs,
not a routine memory approval or curation inbox.

The immutable receipt contains `schema: atc.principal-project-binding.v1`,
`state: bound`, `canonical_project_id`, `binding_id` and integer `version: 1`.
Bindings and project identities have immutable identity fields. Revocation is
one-way; a required binding cannot be downgraded to a legacy client. An existing
identical active registration is idempotent. Reassignment requires another
principal, not mutation of a receipt. A missing, revoked, foreign, unauthorized,
expired, future or changed anchor fails closed. No fallback to broad retrieval
occurs. Principal revocation continues to use existing authentication behavior.

## Capture, replay and delivery

Core resolves the binding again inside the capture transaction, strips any
incoming internal binding field and records its own receipt in the normalized
event before ledger staging. Capture responses read that committed receipt,
including on replay. Raw observations and formed records receive the exact
project scope. Existing formed user slots are partitioned by the canonical key
so a capture in B cannot replace A's preference slot. This stamps capture origin;
it does not establish that a personal statement is globally applicable or
project truth. The extractor, mixed-claim formation policy and completeness
gate are unchanged.

Search/bootstrap selection requires exact membership in the registered scope
and exactly one project scope. This applies before relevance and to the
mandatory preference tier. Ambiguous multi-project records and unclassified
legacy/global preferences cannot enter a bound pack. Existing record ACLs still
apply. Bound capsule construction reads only the same project's authorized
truth; activation uses registered scope membership rather than hints or labels.
Direct record and truth routes observe that boundary too.

Bootstrap reports the binding separately from `project_context`.
`GET /v1/context/project-binding` exposes the authenticated reader's receipt.
Legacy clients report `state: legacy_unbound` and retain their previous behavior;
legacy capture keeps its previous response shape. Internal Core/importer calls
without a principal retain their existing authority. Unregistered principal
objects do not create a legacy exception.
Existing remote Edge identities use their separate Core approval registry and
remain explicitly legacy. They cannot use the new local-principal binding API;
unapproved, foreign or revoked remote identities fail closed.

## Migration, portability and limits

Schema 21 adds local identity/binding tables and the required-binding flag.
Legacy registrations begin unbound. Migration is transactional and tolerates
an already-added column without a migration marker. Export/restore accepts
schema 21 but excludes local identities, bindings and registrations. Restored
memory retains its project scopes; it cannot mint local application authority.

Read principals separately granted `context:status` can still see vault-wide
aggregate coverage counts. This is not an aggregate-metadata isolation change.
Ongoing requests use existing read consistency; this is not a new linearizable
revocation guarantee. There is no binding repair/refresh, anchor reassignment,
project merge, import-to-identity mapping, application registration wizard or
global-personal-memory policy in this slice. Existing source rebuild semantics
are retained; a missing anchor fails closed rather than authorizing a substitute.
Recreating bindings after restore also needs explicit identity/slot reconciliation:
formed records retain their prior canonical-key partition, while a fresh local
registration issues a new key. Restore-to-new-binding correction is unsupported
and unrun; the portability test proves data retention without access authority.

## Validation

Validation uses disposable synthetic vaults and in-process authenticated HTTP.
The 36 new integration cases cover equal labels/distinct identities, immutable
receipts, mandatory preferences, capsule and direct-route isolation, caller
metadata rejection, capture transaction races, revoked/missing/stale/foreign
authority, same-slot captures in two projects, replay, cold Core deletion,
schema migration and export without local authority. Both actual hook retrieval
helpers are exercised over that synthetic transport. Installed clients and
reader-model behavior are **UNRUN**.

Existing synthetic retrieval fixtures now register their preexisting reader
IDs so new fail-closed registration checks do not create a test-only authority
exception. Fixture texts, ACL identities, expected results and the frozen v1
acceptance adapter/runner/grader remain unchanged. Holdout execution and its
integrity-only reader are excluded. Corrected runtime
`01e1f5cfe514b314fd302992a2b9efd73a11ac1c` passes 78 targeted checks,
including all 36 new cases, and 3,577 broader tests with 21 skips and three
warnings. The broad run explicitly excludes the sealed-holdout integrity reader
and two native registry tests previously reproduced failing unchanged base with
Windows error 6801. The first broad run found an approved-Edge compatibility gap
and an unregistered experimental viewer, both corrected in this runtime. Scoped
independent review is closed without a concrete blocker and did not run tests.
Ruff check/format, production plus strict new-test/helper mypy (117 files),
documentation and whitespace checks pass on the tested source.

The exact-runtime frozen development diagnostic remains 4/14 with zero operation
failures and expected exit 1. All checkpoint outcomes, failure details and
observations match delivery-status runtime `2affdc7b`. Its unchanged adapter
does not activate these opt-in bindings; this is regression evidence, not a
project-isolation score. No post-score tuning occurred. Native/release gates,
installed clients/model and restore-to-new-binding correction remain unrun or
unresolved as disclosed above. Exact receipts accompany the local delivery.
