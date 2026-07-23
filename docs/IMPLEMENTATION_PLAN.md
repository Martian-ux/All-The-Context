# Implementation plan

## Product transition

ADR-039 replaces the review-first lifecycle with one-time setup and automatic,
reversible, provenance-backed context maintenance. The implementation is
complete only when all slices below are integrated and the final verification
gate passes; landing documentation or an isolated schema does not by itself
make the behavior implemented.

## Slice 1: policy contract and storage

1. Add the versioned `automatic-v1` decision policy and the
   `staged`/`applied`/`reinforced`/`tentative`/`ignored` dispositions.
2. Extend observations with `observed_at` and Core-derived origin, then persist
   `record_id`, `decision_reason`, `decided_at`, and `policy_version`.
3. Evaluate ordinary observations in a single logical transaction that stores
   evidence and creates, updates, reinforces, or declines current context.
4. Keep only applied current records retrieval-eligible.
5. Make explicit correction immediate and preserve immutable prior versions;
   keep delete/restore reversible and purge separately irreversible.
6. Map existing approved/rejected/unresolved data to
   applied/ignored/reevaluated state with an idempotent checkpointed migration.

## Slice 2: client and MCP behavior

1. Keep `propose_memory` as the compatibility submission name while changing
   its product meaning from review candidate to automatically evaluated
   observation.
2. Return `id`, optional `record_id`, disposition, `decision_reason`,
   `decided_at`, `policy_version`, and replay state.
3. Derive effective origin and trust inside Core; never accept a requested
   disposition from a client.
4. Update MCP instructions and client examples so normal context retrieval and
   observation submission happen automatically after one-time connection.
5. Preserve Relay as a queue/projection only; it never runs the policy or
   creates current context.

## Slice 3: provider import

1. Stage extracted observations until `finish_ingestion` validates and stores
   the truthful coverage report.
2. Publish eligible user-authored observations automatically only after
   successful completion.
3. Keep provider-synthesized and generic instruction-bearing memory tentative,
   exclude assistant, system, tool, and attachment roles, ignore secret-like
   material, and never execute imported text as instructions.
4. Preserve raw-source recovery, parser-version replay, bounded streaming, and
   idempotent decisions.
5. Report applied, reinforced, tentative, ignored, skipped, and coverage counts
   without presenting a review inbox.

## Slice 4: dashboard and user experience

1. Remove Review navigation, badges, pending counters, approval forms, and
   "ready for review" copy.
2. Make Context the default useful surface and describe imports in terms of
   automatic outcomes.
3. Add optional Activity/provenance inspection with no required actions.
4. Expose correction, reversible forget/delete, restoration/undo, evidence,
   and version history from Context.
5. Keep setup to the client connection and the minimum meaningful privacy
   choice; do not make users tune a scoring or trust system.

## Slice 5: integration and release evidence

1. Cover the complete decision matrix, duplicates, conflicts, corrections,
   retries, migration, restart, restore, and permission isolation.
2. Prove failed/unfinished imports cannot modify current context and
   tentative/ignored/staged observations cannot enter retrieval.
3. Update the reproducible demo and fresh-install browser smoke so neither uses
   an approval endpoint or dashboard review step.
4. Run `python -m ruff check .`,
   `python -m mypy packages/allthecontext/src`, and `python -m pytest`.
5. Run dashboard tests, TypeScript checks, production build, dependency audit,
   and the cross-platform package matrix.
6. Record observed results in `docs/STATUS.md` and
   `docs/REQUIREMENTS_TRACEABILITY.md`; do not carry old review-first evidence
   forward as automatic-policy evidence.

Embeddings and any new synchronization service remain deferred until the
install, update, automatic-maintenance, and direct-Core security boundaries are
accepted.

## Future policy extension

`automatic-v1` corroborates tentative observations but does not expire or decay
them. Configurable tentative retention/decay may be designed as a later
versioned policy after the beta gates pass. It must preserve noncurrent
isolation, deterministic replay, provenance, export/restore behavior, and the
no-inbox product contract.
