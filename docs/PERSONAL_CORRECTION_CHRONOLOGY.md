# Personal correction framing and chronology

The isolated October 11 repair starts from the registered named source-client
journey `46b3c552aa661bdb0324f4dcd9a043540299f8e6`. That successor includes the
earlier preference-facet, project-binding, bounded-decision and partial-recall
repairs. Its unchanged frozen development audit still passes 3/14 checkpoints,
with zero operation failures. The historical 2/14 report is not the current
baseline.

## Reproduced failure and bounded change

An otherwise supported direct correction such as `I prefer thorough responses
now, rather than quick responses.` was left raw because terminal `now` became
part of the response-style value. Remove that terminal framing word only when
the complete turn already matches the explicit `instead of` / `rather than`
correction structure. Preserve the entire original turn in record content and
source evidence. Existing same-facet subject checks still reject uncertainty,
negation, quotations, attribution, task qualifications and mixed turns. A plain
`I prefer thorough responses now.` remains unsupported.

Personal corrections previously inherited the general inclusive timestamp rule.
Public synthetic captures prove that equal-time and future-dated corrections
could replace the current preference. An explicit contrast now requires an
observation time strictly later than the current predecessor and no later than
the capture transaction's time. Compare aware datetimes; missing/invalid/naive
chronology fails closed. The guard runs under the existing capture write lock,
after exact old-value and partition checks, before the existing Core-owned
record-ID/version compare-and-swap. Ordinary equivalent evidence retains its
existing reinforcement behavior.

Equal-time and future corrections remain tentative raw/formed evidence with the
existing closed `personal_preference_stale` reason. Older corrections keep the
ordinary ignored-evidence path; the existing timestamp rule prevents replacement.
Event replay retains the committed receipt. No schema, migration, binding grant
or predecessor authority changes.

## Synthetic verification and remaining gates

`tests/integration/test_personal_correction_chronology.py` adds independent
regressions without importing evaluator fixtures or holdout answers. It checks
complete correction framing, qualification and cross-facet rejection, equal/
older/future chronology, delayed ABA, two capture clients, fresh readers, actual
Codex/Claude reference formatters, current source/history, closed Core reopen,
integrity rebuild, replay and typed forgetting. The Claude journey also supplies
an offset-aware observation timestamp.

The first host baseline run produced 18 failures and 16 passes. Twelve failures
expose the unsupported framing, two expose actual equal/future replacement, two
expose the older/ABA ignored-versus-tentative disposition, and two stop the durable
journeys at the missing correction. Older/ABA replacement was already prevented
by the general chronology rule. Those diagnostic differences are not reported
as additional stale-value repairs. The first candidate focused run exposed an
existing delayed-correction assertion requiring the ignored disposition. The
repair preserves that assertion and path. New older/ABA tests likewise require
ignored evidence while preserving all value/version/history/replay checks.

Restricted Core-startup test runs stalled and were stopped after verifying their
owned process identities. Normal host runs use fresh task-owned scratch, null
keyring and isolated settings; no permission changes were made. The local
checkpoint packet records completed focused/static checks and exact source.
The repaired checkpoint passes all 34 new cases plus the two existing delayed
correction/reinforcement checks (36 total). Repository Ruff, changed-file format,
production mypy (116 files), strict new-test mypy, docs and whitespace checks pass.
Full selected-suite validation, the candidate development comparison and
independent exact-source review remain required before integration acceptance.
Frozen evaluator/grader/adapter and sealed holdouts are unchanged. Installed
clients, reader-model behavior and live personal memory are untested.
