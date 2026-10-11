# Historical capture and portable raw erasure

The independent review of `ec17f6e2` verified direct event erasure and opaque
retries. It also reproduced two inherited gaps: new event identifiers could
recapture a purged field's historical evidence, and portable export detached the
event relationship needed to erase its raw sibling after restore. Canonical
tombstones alone did not protect raw observations.

Schema 22 adds two content-free relationships. `memory_erasure_links`
associates a raw observation ID with the canonical record ID, an opaque project
anchor ID (or fixed unbound-personal partition), and a code-owned opaque field ID.
`memory_purge_barriers` retains the partition, field, erased record ID and
purge time. Neither table contains names, deleted values, quotations, embeddings
or content fingerprints. The field IDs describe the closed Core-supported claim
families; they are derived from schema definitions, never deleted content.
The portable memory ledger uses `memory_` table names. Capture-runtime tables,
authorization sidecars, machine paths, credentials and local event pointers
remain excluded by the existing export contract. The selected broad run caught
the earlier unreleased `capture_` naming collision; its privacy assertion is
preserved unchanged. Schema 22 has not shipped. Earlier local schema-22 snapshots
and packages are diagnostic artifacts, not supported upgrade inputs; use the
schema-21 predecessor for upgrade verification.

Core creates erasure links only from intact formed/user event lineage, validating
the original event fingerprint, application receipt, source/principal/vault,
Core formation metadata, source reference and committed project binding. Capture
checks only its new observation; startup/export can backfill intact older local
events. Imported metadata without the local event tuple cannot mint such links.
These associations authorize erasure closure, never formation, correction,
retrieval, project routing or grants. Portable restore validates their closed
shape and package-owned typed targets; machine-local credentials and bindings
remain excluded. The passphrase-owning backup operator can alter a package; this
format does not independently attest historical authorship.

Purge collects raw IDs through the portable association as well as intact events.
Restoring an older package against a destination purge tombstone filters those
raw IDs before insertion. Purging a newly restored active vault uses the same
association. Unrelated identical evidence is preserved by identity, without text
matching. Links disappear with their record; the content-free chronology fence
survives portable export and recovery cutover. Project partitioning uses the
stable anchor ID, so re-registering a restored anchor under a new local canonical
project ID retains the fence. Project labels never choose the partition.

For a new direct-user event forming a supported field, Core resolves the current
binding and checks the fence in the capture transaction before persisting its
body. An `observed_at` at or before purge, or in the future, is withheld. Core
persists only an ignored `purged_capture_receipt` observation and an opaque event
receipt with a zero payload-hash sentinel. The response reason is
`capture_history_purged`. New event IDs and new bound clients cannot bypass the
field/anchor fence. Existing identity retries consume their original receipt;
altered retry bodies do not form memory or get persisted. Refusal reasons remain
closed codes through reopen and migration repair.

A fresh event dated after purge is eligible under ordinary Core formation rules,
including choosing the same value again. Explicit remember/edit remains a separate
operator action. This is a chronology fence, not a permanent value blacklist.
Missing timestamps use the existing current-time contract. An old copy submitted
with a new or missing timestamp is indistinguishable from a new live choice
without retaining deleted content or adding a stronger authenticated timestamp
protocol. Unsupported wording and non-user turns retain their existing raw
capture behavior; this repair does not claim arbitrary semantic replay detection.

Synthetic tests cover preferences/decisions, equal/older/offset/future chronology,
new choices, cross-project/facet isolation, unbound clients, cold rebuild, stable
opaque retries, both export modes, restored-vault purge, recovery carry-forward,
malformed package refusal before destination mutation, and repeated migration.
The unchanged independent surface/portability assertions are part of validation.
Only schema-count expectations and the unsupported-version boundary in existing
tests advance to 22 and 23 respectively. Frozen auditors and sealed answers are
unchanged. Exact test receipts are supplied separately with the local handoff.

Old completed purge jobs and old packages that already lost their raw/formed
association cannot be safely assigned to an erased record from text alone.
This slice does not fabricate those links, recover lost original receipt IDs,
erase external backups, or claim retroactive cleanup of those legacy orphans.
Intact local pre-22 captures are backfilled before a new export. Legacy packages
without portable links retain the documented gap and need separate recovery
policy. Physical erasure also depends on successful compaction and the external
storage limitations in the [runbook](operations/RUNBOOK.md).
