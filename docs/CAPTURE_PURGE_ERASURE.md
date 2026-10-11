# Linked lifecycle capture erasure

This bounded repair follows the independent audit's confirmed plaintext leak.
Record purge removed the formed observation, versions and canonical record, and
scrubbed capture event payloads, but retained the raw `captured_user_turn` sibling.
That sibling has no canonical `record_id` or observation link. Original and
corrected wording therefore survived completed compaction in `context_candidates`.

Purge now includes candidates from the linked observations' exact durable capture
events, matching the event's source and the current vault. It uses the existing
transactional deletion and shared-record guard, rather than matching content or
hiding rows from recall. Original, corrected and reinforced turns from separate
capture principals are included. Identical evidence in another project remains.
No new memory table, authority rule or migration is introduced.

Deleting the raw sibling also removes the observation previously returned for an
exact retry after purge. Core now retains the originally committed observation
ID and project binding in a closed, content-free event receipt. Exact identity
retries return `status: "replayed"`, the original opaque `observation_id` and
binding, and `reason: "capture_content_purged"`. That ID identifies erased
evidence; observation lookup remains not found. No observation is recreated.

ADR-022 forbids retaining content hashes as purge proof. The old payload digest
is replaced with a fixed zero sentinel in the legacy fixed-width column. Before
erasure, changed payloads still conflict. After erasure, Core cannot compare
retry bodies against forgotten content: the same authenticated event identity,
item, generation, order and idempotency key replay only the opaque receipt,
including when the retry body differs. They cannot form replacement evidence.
Changed identity fields still conflict. Missing observations without a valid
erased receipt remain conflicts. The provider-neutral scrubbed event path also
consumes exact-identity retries without retaining a content fingerprint.

## Synthetic evidence

Before implementation, the unchanged independent plaintext audit failed at
`context_candidates`. Five new assertions failed on the same omission; their
missing-observation control passed. The new regression module checks personal
preferences and project decisions, corrections and reinforcement, interrupted
compaction followed by cold startup, fresh bound readers, integrity rebuild,
all-table plaintext scans, physical SQLite/WAL bytes, opaque identity retries,
no-influence changed bodies and changed-identity rejection. The expanded frozen
audit additionally checks cold admin observation listings, newly created
encrypted exports restored elsewhere, and exact original receipt identity.
Frozen erasure and existing purge/capture/correction controls are also run.
Detailed receipts live beside the isolated checkout. The first implementation
passed six new tests after correcting their HTTP-conflict assumption, but its
nullable observation receipt and retained payload fingerprint did not meet the
expanded frozen matrix. The final design above replaces that interim behavior;
its validation is recorded separately, without modifying frozen assertions.
The expanded run completed with 135 passing checks and two retained historical
recapture failures. The final six new cases, including forgotten payload-digest
scans, pass; the unchanged first-pass audit is 16 passed/10 failed. Those ten
assertion failures remain visible: six natural-language capability gaps, one
historical recapture case, and three invalid NULL-record assumptions (the separate
chronology controls confirm unchanged current truth). The two expanded historical
cases cover the original and a newly bound principal. They are not green erasure
acceptance. Broad selected-suite/static receipts accompany the handoff.
No live personal vault, sealed answers, installed clients or model readers are
used. Evaluation expectations are unchanged.

## Remaining boundaries

This repair governs newly executed purges. It does not retroactively recover the
lost record/event relationship of purge jobs already executed by the old code.
Replaying those completed job IDs remains idempotent; a separate explicit repair
would need to identify and validate their orphaned scrubbed capture events.

The independent audit also found that historical evidence submitted by a new
capture principal under new event identifiers can form a new record after purge.
That defect remains unresolved for both original and new bound principals here.
Stable-ID tombstones protect existing
records, exports and exact retries; they do not supply a portable, verified
cross-principal historical formation barrier. Designing that barrier requires
separate authority, retention and recovery review. This change does not claim
complete prevention of fresh historical recapture or complete deletion of
external backups. SQLite physical erasure remains subject to the documented
compaction and external-storage limits in the [runbook](operations/RUNBOOK.md).
