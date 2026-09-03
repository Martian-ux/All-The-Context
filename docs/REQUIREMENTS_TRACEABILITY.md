# Requirements traceability

"Implemented" means exercised by repository tests. Protected-main CI and
CodeQL evidence is credited only when bound to an exact SHA; exact artifact,
live/private client/provider, and release acceptance remain separate gates.
Earlier evidence is retained only as historical context and does not become
evidence for this checkout.

The UPDATER-04 and UPDATER-05 rows below refer to ADR-184, the startup-
recovery decision in this checkout. ADR-183 is retained for the separately
merged packaged-uninstall observation decision. The follow-up JSON parser
containment row refers to ADR-186 and preserves the distinct optional-marker
boundary in ADR-185. The primary updater metadata-authority row refers to
ADR-187 and starts from the exact `origin/main` merge base
`466b5027a66cf7a8dba4ec0bb79b8b9af72cc9eb`. Its recovery, keyring, cleanup,
and privacy remediation is covered by ADR-188 and ADR-189.
The invalid active-journal authority and operator private-key read follow-up
is covered by ADR-191 and ADR-192.
The terminal publication lifecycle and numeric version containment follow-up
is covered by ADR-193 and ADR-194.
The P1 pre-binding crash boundary and authenticated retirement replay are
covered by ADR-195.
The completed-identity operation-binding containment follow-up is covered by
ADR-196.
The bounded efficiency hardening follow-up is covered by ADR-198.
The Windows hardening wave integration and check-only updater boundary are
covered by ADR-199. Its four cherry-picked worker commits are preserved in
the integration ancestry; the final integration commit adds only bounded
configuration/lifecycle safeguards and documentation.

### 2026-09-03 Windows hardening wave integration

| Requirement | Implementation/evidence | Status |
|---|---|---|
| OTA-WIN-01 — Windows OTA must bind one canonical four-component package to signed release metadata and installed targets | `installed_component_manifest.py`; `updater.py`; `windows_update_helper.py`; `release/installed-component-manifest.schema.json`; updater/helper manifest regressions | Implemented at source level. The release-metadata-bound archive is bounded to setup plus canonical manifest/checksum; manifest version/package identity and fixed main/MCP/recovery/updater descriptors are independently validated before forward health. Rollback retains separate journaled prior-component digests. No migration is added, and artifact/clean-machine acceptance remains separate |
| OTA-WIN-02 — native Windows build provenance must be deterministic, relative, and independently comparable | `scripts/native_build_provenance.py`; `scripts/verify_reproducible_build.py`; `release/native-build-provenance.schema.json`; `scripts/build_desktop.py`; CI/release/replacement workflows; provenance and workflow tests | Implemented as a source/workflow contract. Two clean builds use pinned Python 3.12.10, PyInstaller 6.21.0, uv 0.11.32, and reviewed lock digests; only matching byte/size identities are staged. The receipt is separate provenance evidence and does not establish signing, Defender, release, clean-machine, or downloaded-candidate execution evidence |
| OTA-WIN-03 — unattended update checking must be conservative, single-flight, retry-bounded, and check-only | `UpdateAutomation`; `UpdateAutomationConfig`; `UpdateAutomationPolicy`; Core lifespan/configure wiring; updater regressions | Implemented at source level and locally tested. The worker runs only for an enabled configured channel, uses serialized manager checks and bounded cadence/backoff, and has no download/install/process-launch/task/service/shutdown/reboot authority. Automatic install and restart remain disabled; the integration also caps retry attempts and lifecycle join timeouts |
| CI-WIN-01 — loaded Windows scheduled-capture acceptance needs only the bounded asynchronous poll widening | `tests/unit/test_scheduled_packet_f_local_source_acceptance.py`; focused acceptance regression | Implemented as a test-bound-only change. No production capture or acceptance semantics are changed |

Focused integrated updater/helper/manifest/packaging/provenance/workflow
validation after the integration safeguards passed 586 tests with 5 expected
filesystem-capability skips; updater-only rerun passed 177 with 2 expected
filesystem-capability skips. Ruff and mypy passed. Final repository-wide
counts, collection parity, documentation/schema checks, package/build checks,
and hosted checks must be recorded against the final exact pushed SHA.

The distribution plan remains unsigned: SmartScreen warnings are accepted,
but Defender quarantine/deletion is not. Source contracts, hosted results,
artifact-level scanning, and clean-machine evidence are distinct evidence
classes; none is credited here until it is independently produced and bound.

### 2026-09-03 bounded efficiency hardening

| Requirement | Implementation/evidence | Status |
|---|---|---|
| PERF-01 — multi-observation transactions must not rebuild all integrity groups once per observation | `CoreStore._evaluate_observation_tx`; `finish_ingestion`; `evaluate_staged_observations`; `publish_source_rebuild`; batch recomputation regressions in `test_automatic_context_policy.py` and `test_memory_truth.py`; ADR-198 | Implemented and locally tested. Each qualifying batch defers the existing integrity request and performs one transaction-final recomputation; single-observation callers remain immediate and final observation dispositions/records are asserted |
| PERF-02 — retrieval conflict lookup must scale with the bounded candidate set | `retrieval.py::_conflict_states`; migration `019_integrity_member_reverse_lookup.sql`; targeted trace and query-plan regression in `test_memory_integrity_purge.py`; ADR-198 | Implemented and locally tested. Requested IDs are deduplicated and queried in 500-ID chunks through `(record_id, group_id)`; unrelated memberships are not loaded |
| PERF-03 — bounded integrity listing and audit writes must avoid avoidable connections/queries | `CoreStore.list_integrity_groups`; `CoreStore._vault_id_tx`; `CoreStore._audit`; query-count and connection-count regressions in `test_memory_integrity_purge.py`; ADR-198 | Implemented and locally tested. One membership query serves the at-most-500-group page, and audit insertion reuses its caller transaction |
| PERF-04 — dashboard polling must not overlap or update after lifecycle stop | `api.ts::importSource`; `App.tsx::refreshStatus`; completion-driven timer and fake-timer regressions in `api.test.ts` and `App.test.tsx`; ADR-198 | Implemented and locally tested. The next request is scheduled only after settlement, in-flight status refreshes are shared, late import callbacks are suppressed, unmount cancels polling, and hidden documents receive no periodic status work |

The Windows Python CI execution candidate is covered by ADR-197. It changes
test scheduling only: the collection contract must prove the sequential and
four-worker file-level nodeid sets are identical before the existing full
Python gate runs. Hosted timing, flake, and branch-protection evidence remain
separate post-merge checks.

### 2026-09-03 Windows Python test execution acceleration candidate

| Requirement | Implementation/evidence | Status |
|---|---|---|
| CI-TEST-01 — the accelerated Python gate must execute the complete required test set without selector-based omissions | `scripts/check_test_collection.py`; `.github/workflows/ci.yml`; `tests/unit/test_pytest_collection_contract.py`; `pytest-xdist>=3.8,<4` in `pyproject.toml` and `uv.lock`; ADR-197 | Implemented locally. The collection proof compares sequential and four-worker `--dist=loadfile` nodeid sets and fails on collection errors, empty output, or any difference. The initial isolated Windows benchmark collected 2,686 nodeids across 169 files (2,673 pass outcomes and 13 capability skips); after the three contract tests were added, that worker branch collected 2,689 items and passed 2,676 with 13 skips in 277.84 seconds of pytest time. Its required sequential fallback passed the same set in 749.42 seconds. After integration added the efficiency regressions, the final branch collected 2,696 identical sequential/four-worker nodeids; its full sequential run passed 2,683 with 13 skips in 828.88 seconds, and an independent full four-worker run on the code-identical pre-documentation SHA passed the same set in 296.21 seconds. Three repeated Windows isolation-sensitive four-worker runs each passed 446 and skipped 3. The existing check name, full suite, platform skips, and downstream build/security steps remain unchanged. Hosted timing, post-merge p95/flake evidence, and final branch-protection confirmation remain required after merge. |

The startup-recovery evidence also includes install-root and install-parent
reparse simulations before forward child launch and rollback copy/replace;
privileged native junction creation remains host-capability dependent.

The follow-up diagnostic boundary is covered by ADR-185. The marker is
optional, content-free, with at most 16 KiB plus one sentinel byte requested at
the read boundary and at most 16 KiB accepted, and never startup authority;
writer failure, malformed/oversized/unreadable/non-regular/reparse markers,
hostile parents, and probe/read races resolve to a controlled result (missing
markers remain absent) without a traceback or path/raw-state disclosure. This
narrows diagnostic failure containment only and does not remove the documented
handle-based filesystem race residual for the recovery authority itself.

ADR-184 also covers complete persisted-state schema validation, including
fail-closed handling for malformed inactive phases; missing state is blocked
when the plain `updates/transactions` root contains evidence, while an absent
or empty root permits an ordinary first start. Journal-bound storage is
checked as a complete path family (transaction, backup, database, state,
staging evidence, install/helper/target, and SQLite sidecars), with the
boundary repeated immediately before replacement or deletion. Candidate
journal publication prevents a pre-cutover transition failure from mutating
the parent or backup identity used by abort recovery. These checks block
existing reparse redirection; concurrent same-user mutation between a check
and the following filesystem syscall remains an explicit residual race because
the architecture does not claim handle-based no-follow atomicity.
Prospective write targets may create only a contiguous missing tail below the
deepest verified plain ancestor, one component at a time, followed by complete
chain revalidation before the write or child launch; existing reparse paths are
not treated as missing.

ADR-186 covers parser failures after those bounded reads. The decoder maps
invalid UTF-8, malformed JSON, integer digit-limit `ValueError`, and deep-parser
`RecursionError` to `metadata_unreadable`, retains `metadata_invalid` for
non-dict roots, catches no process-control or arbitrary programming exception,
and leaves the limit-plus-one read contract unchanged. Caller outcomes remain
fail-closed or controlled at their existing authority boundary.

ADR-187 covers the primary `UpdateManager` lifecycle after the helper parser
boundary. Preferences, state, and staged manifests use explicit 16 KiB, 64 KiB,
and 128 KiB limits with one sentinel read, descriptor/path identity checks, and
plain-file/parent-chain containment. Atomic replacement preserves an existing
target when the target or parent is unsafe. Manifest consumers revalidate the
persisted identity, signature/keyring, channel, platform, architecture, version,
and state binding before using URL/size or entering export, preflight, backup,
or handoff. The signed artifact size is also bounded before use. Unsafe state blocks later writes/network operations and preserves
recovery evidence; public parser failures remain bounded and content-free.

ADR-188 covers the remediation boundary after that lifecycle hardening:
incomplete active recovery objects are preserved byte-for-byte and cannot
trigger cleanup or state saves; the bundled keyring is bounded and
identity-checked; staging/transaction/export/temporary-file cleanup is bounded
and race-refusing without recursive path deletion; and all public updater
errors are fixed or classified without raw manifest values, keyring paths,
credentials, URLs with credentials, or exception text. Focused updater,
manifest, and helper tests include real oversized/deep/invalid-UTF8/non-object
inputs, exact/multibyte reads, non-regular paths, partial active state, root
replacement, and atomic parent replacement. A source-built disposable Windows
packaged smoke passed through rollback and uninstall.

ADR-189 extends that boundary to terminal recovery cleanup: an ambiguous
cleanup keeps the transaction pointer and active retryable state rather than
persisting pointerless `installed`/`rolled_back`; all updater unlink paths
perform final parent/entry identity checks; and scripts/release_keyring.py
uses bounded readers for keyrings, reviewed public keys, and tracked audit
files, including raw comparison, rollback, and post-write verification. ADR-190 adds an iterative global cleanup budget of 32
removable entries and 32 directory levels, preflighted before mutation so
over-budget or pathological trees preserve all evidence. New regressions cover
forced cleanup refusal, deterministic parent replacement, exact-limit
multibyte script reads, oversized raw keyring comparison, deep trees, wide
trees, budget boundaries, and RecursionError. Hosted follow-up checks must bind
to the corrected commit.

The 2026-09-03 PR #110 final-review integration is covered by ADR-195; it
adds keyed recovery authority, explicit terminal side-effect behavior,
pre-binding crash reconciliation, and authenticated tombstone retirement while
retaining the local trust and filesystem-race residuals below.

### 2026-09-03 PR #110 final-review remediation integration

| Requirement | Implementation/evidence | Status |
|---|---|---|
| UPDATER-13 — a completed terminal identity must retain a valid operation binding and authenticated journal/tombstone evidence until safe retirement | `updater.py::UpdateManager::_completed_recovery_binding_is_valid`; `updater.py::UpdateManager::_validate_internal_state`; `updater.py::UpdateManager::_clear_completed_recovery_evidence`; `updater.py::UpdateManager::_prune_retirement_tombstones`; `windows_update_helper.py::_retirement_tombstone_is_authoritative`; `windows_update_helper.py::ensure_recovery_before_core`; `tests/unit/test_updater.py::test_completed_identity_without_operation_blocks_lifecycle_mutations`; `test_completed_identity_without_operation_blocks_pruning_and_preserves_authority`; `test_completed_identity_without_operation_is_deterministically_blocked_on_restart`; `test_completed_identity_with_forged_operation_preserves_bound_evidence`; `test_intact_completed_binding_retires_after_transaction_tree_removal`; `tests/unit/test_windows_update_helper.py::test_core_start_guard_blocks_live_retirement_authority_after_tree_removal`; `test_core_start_guard_rejects_completed_identity_without_operation_reference`; `test_core_start_guard_allows_removed_tree_after_intact_retirement`; ADR-196 | Implemented locally. Non-null completed identity now requires a valid lowercase-hex operation ID, terminal phase, cleared transaction pointer, and exact authenticated terminal journal or canonical bounded tombstone binding. Missing/corrupt/forged operation references preserve state and authority, disable mutation, and block cleanup/pruning; valid bounded retirement after tree removal remains retryable and idempotent. Frozen startup stays blocked when pointerless tombstone evidence still has live, unavailable, or invalid credential authority and permits the already-retired case only after the same binding checks. Local source/test and broader package/release gate counts are recorded in the final status entry; hosted checks must bind to the final pushed SHA. |
| UPDATER-10 — terminal recovery authority must be authenticated independently of recomputable metadata and must survive post-publication failures | `windows_update_helper.py::bind_recovery_authority`; `windows_update_helper.py::seal_terminal_recovery_authority`; `windows_update_helper.py::validate_recovery_authority`; `windows_update_helper.py::_transition_handoff_state`; `windows_update_helper.py::_commit`; `windows_update_helper.py::_rollback`; `updater.py::_clear_completed_recovery_evidence`; `updater.py::configure`; `updater.py::defer`; `updater.py::check`; `updater.py::clear_error`; `ensure_recovery_before_core`; `tests/unit/test_updater.py::test_pointerless_recovery_rejects_recomputed_identity_forgery`; `test_completed_cleanup_retires_authority_after_tree_removal`; `test_clear_error_preserves_failed_retirement_evidence_and_retries`; `test_clear_error_rejects_missing_retirement_tombstone_after_failed_retirement`; `test_clear_error_rejects_tampered_retirement_tombstone`; `test_clear_error_clears_ordinary_non_recovery_error`; `tests/unit/test_windows_update_helper.py::test_same_operation_forged_mutable_authority_is_rejected`; `test_terminal_replay_requires_completed_journal_binding`; ADR-195 | Implemented locally. A per-operation OS-credential-store secret HMAC-authenticates the immutable journal identity and terminal phase; plain journal/identity hashes cannot forge authority. State-first publication and pending/current identity reconciliation preserve the terminal outcome across crashes. After keyed `COMMITTED` or `ROLLED_BACK` publication, state persistence, unregister, credential retirement, staging/transaction cleanup, or Core launch failure is degraded/retryable follow-up and cannot trigger rollback. Completed identity is retired only after safe staging and transaction cleanup; a bounded authenticated tombstone bridges every cleanup/credential-deletion crash window, while failure preserves authority and blocks ordinary mutation. `clear_error` invokes this same retirement path before clearing error state, raises on failed retirement, preserves the cleanup error/evidence for retry, and is idempotent after success; ordinary non-recovery errors still clear. Frozen startup and `UpdateManager` accept the same validated tombstone semantics. |
| UPDATER-11 — orphan transaction cleanup must distinguish empty pre-authority directories from credible recovery evidence | `windows_update_helper.py::_transaction_entry_has_recovery_evidence`; `updater.py::_transaction_evidence_requires_preservation`; `updater.py::_clear_completed_recovery_evidence`; `updater.py::_prune_retirement_tombstones`; `tests/unit/test_updater.py::test_pre_authority_transaction_directories_are_reclaimed_after_handoff_failure`; `test_partial_journal_creation_keeps_recovery_authority_after_restart`; `test_malicious_nonempty_transaction_directory_remains_fail_closed`; `test_successful_terminal_recovery_removes_staging_and_transaction_evidence`; `test_retirement_tombstone_orphan_is_reaped_by_startup_pruning`; ADR-195 | Implemented locally. Empty operation directories and empty nested directory structure may be reclaimed before authority exists. Journals, regular files, links/reparse objects, non-directories, and ambiguous/pathological trees remain credible evidence and are preserved fail-closed. Successful terminal recovery removes the staged artifact/manifest and transaction tree before retiring the authority, while retaining the canonical database, verified backup, and required rollback/source material. Orphan tombstones are removed only after bounded canonical-payload, journal-digest, terminal-HMAC, and already-retired-credential checks. |
| UPDATER-12 — pre-binding and authority-bound/state-unbound crashes must recover or reclaim safely across repeated frozen startups | `windows_update_helper.py::prepare_handoff_state`; `windows_update_helper.py::bind_recovery_authority`; `windows_update_helper.py::bind_handoff_state`; `windows_update_helper.py::_reclaim_prebinding_transaction`; `windows_update_helper.py::ensure_recovery_before_core`; `updater.py::PlatformInstaller.handoff`; `tests/unit/test_updater.py::test_windows_adapter_prepares_strict_journal_before_detached_handoff`; `tests/unit/test_windows_update_helper.py::test_core_start_guard_reclaims_prebinding_crash_and_allows_new_start`; `test_core_start_guard_reclaims_empty_prebinding_tree_after_cleanup_crash`; `test_core_start_guard_preserves_partial_prebinding_journal`; `test_core_start_guard_preserves_invalid_prebinding_authority`; `test_core_start_guard_preserves_extra_prebinding_evidence`; `test_prebinding_handoff_transition_is_idempotent`; ADR-195 | Implemented locally. The pending identity is recorded before credential creation; a valid authority-bound journal with an unbound state is reconciled and relaunched, while a strictly empty expected pre-binding tree is reclaimed and its authority is retired. Repeated startups are idempotent. Partial/malformed/malicious/extra/unstable evidence is preserved byte-for-byte, frozen Core remains down, and the updater cannot clear, configure, defer, check, install, or prune around the unresolved authority. |

The integrated evidence is limited to sanitized synthetic/disposable local
state. The OS credential store and its local ACL are trusted; a host
administrator or compromised credential store is outside this authority model.
Final parent/entry checks still do not provide handle-based no-follow
atomicity against concurrent same-user mutation and the final Windows
filesystem syscall. Hosted checks, exact candidate-artifact acceptance,
signing, publication, release, Defender, Microsoft, and downloaded-candidate
execution remain separate gates.

Local validation for this integration passed 287 adversarial updater/helper
tests (3 expected Windows capability skips), 573 focused updater/helper/
recovery/manifest/desktop tests (6 expected capability skips), and 2,653 tests
in the full suite (13 expected capability skips plus 2 existing Starlette
deprecation warnings). Ruff, format, mypy over 107 source files, documentation,
and diff checks passed; desktop/package/recovery/first-run lifecycle smokes
and release-keyring validate/audit passed as well.

### 2026-09-02 Terminal recovery publication authority and numeric version containment

| Requirement | Implementation/evidence | Status |
|---|---|---|
| UPDATER-09 — terminal recovery must require helper-confirmed journal/identity transitions and preserve authority across post-journal failures | `windows_update_helper.py::_commit`; `windows_update_helper.py::_rollback`; `windows_update_helper.py::completed_transaction_is_authoritative`; `updater.py::_clear_completed_recovery_evidence`; `updater.py::_transaction_evidence_requires_preservation`; `tests/unit/test_updater.py::test_valid_terminal_publication_is_cleaned_before_new_operation`; `test_recovery_cleanup_failure_does_not_overwrite_helper_terminal_publication`; `test_handoff_failure_after_journal_persistence_keeps_recovery_authority`; `test_rollback_failure_keeps_authority_across_error_clear_configure_and_restart`; `tests/unit/test_windows_update_helper.py::test_terminal_replay_requires_completed_journal_binding`; `test_terminal_journal_requires_state_first_terminal_phase`; ADR-193 | Implemented and locally tested. Pointerless `installed`/`rolled_back` state is accepted only when the expected operation is the sole transaction directory and the helper-confirmed journal phase, handoff identity, and `transaction_outcome` all match. The primary updater retires only that proven terminal evidence; invalid or ambiguous roots and registration, launch, rollback, or cleanup failures preserve pointers/journal/evidence, expose fixed errors, block clear/configure/check/defer/install/prune/new operations, and keep frozen Core startup blocked. The focused suite passed 363 tests with six expected capability skips; the full suite passed 2,618 tests with 13 expected capability skips and two warnings. Local/disposable evidence only; hosted checks and handle-based no-follow atomicity remain open |
| RELEASE-VER-01 — numeric release-version parsing must enforce explicit length/component/digit bounds and contain conversion failures | `release_manifest.py::ReleaseVersion.parse`; `windows_update_helper.py::_validate_startup_state`; `windows_update_helper.py::UpdateJournal.validate`; `windows_update_helper.py::record_startup_recovery_parser_failure`; version-bound and parser-failure regressions; ADR-194 | Implemented and locally tested. Version text is limited to 64 characters, four dotted numeric components, and 18 digits per component before conversion. Journal/helper/startup boundaries catch numeric `ValueError` and emit fixed content-free diagnostics without raw version text. Ruff, mypy, the focused/full suites, packaged first-run/restart/rollback/uninstall smoke, desktop build/artifact inspection, and release-key validation/audit passed. The bounds do not claim an independent cryptographic parser or CPU/depth budget, and no signing, release, or candidate-execution acceptance is implied |

### 2026-09-02 Invalid active-journal authority and private-key read containment

| Requirement | Implementation/evidence | Status |
|---|---|---|
| UPDATER-08 — invalid active recovery journals must not revoke recovery authority | `UpdateManager::_active_recovery_evidence_complete`; `UpdateJournal.load(..., validate_storage=False)`; `UpdateJournal.validate`; `journal_handoff_identity`; `tests/unit/test_updater.py::test_invalid_active_recovery_journal_preserves_authority_across_lifecycle`; `test_valid_active_recovery_journal_remains_recoverable`; ADR-191 | Implemented and locally tested. Active `installing`/`restart_required` state requires operation-scoped, plain, non-empty artifact, backup, journal, and directory evidence; the narrow cross-platform storage mode still validates journal schema/phase, transaction identity, absolute paths, operation and backup containment, storage chain, version/state/database/backup/helper bindings, handoff identity, and permitted phase. Malformed, incomplete, oversized/deep, wrong-operation, wrong-phase, or inconsistent journals preserve state/journal bytes and surviving evidence, set the fixed `RECOVERY_EVIDENCE_INCOMPLETE_ERROR`, disable later updater mutation, and keep frozen Core startup blocked. The integrated focused suite passed 281 tests with four expected capability skips; the full suite passed 2,587 tests with 13 expected capability skips. Remaining evidence is source-level/disposable local evidence only; hosted checks, exact-artifact/vendor acceptance, and handle-based no-follow atomicity remain open |
| RELEASE-KEY-01 — operator private-key loading must be bounded, single-pass, and content-free on failure | `release_manifest.py::read_private_key_bytes`; `release_manifest.py::load_private_key`; `scripts/release_manifest.py::load_encrypted_private_key_interactive`; `tests/unit/test_release_manifest.py::test_private_key_reader_accepts_exact_limit`; `test_private_key_loader_rejects_limit_plus_one`; `test_private_key_loader_reads_once_with_bounded_size`; ADR-192 | Implemented and locally tested. Path input is read once in binary mode with one `16 KiB + 1` overflow-sentinel request; empty/oversized input produces fixed `ManifestError` messages, bounded bytes are parsed, and the interactive release utility reuses the same snapshot after its encrypted PKCS8 marker check. No key path, raw key, or exception text is projected. The bound does not claim a separate cryptographic parser or CPU/depth budget, and no signing or release acceptance is implied |

### 2026-09-02 Primary updater metadata authority containment

| Requirement | Implementation/evidence | Status |
|---|---|---|
| UPDATER-07 — primary updater metadata must remain bounded, path-contained, and revalidated before authority use | `updater.py::_decode_bounded_json`; `updater.py::_read_bounded_json`; `updater.py::_atomic_json`; `UpdateManager::_revalidate_persisted_manifest`; recovery helper plain-file/directory, unlink, and atomic primitives; `scripts/release_keyring.py::_read_bounded_keyring_bytes`; focused updater, manifest, helper, and release-keyring regressions; ADR-187/ADR-189/ADR-190 | Implemented and locally tested. Preferences/state/manifest reads and every script-side keyring read use explicit limits with one sentinel read; expected UTF-8/JSON parser failures become stable errors while process-control and unexpected programming failures propagate. Reparse/non-regular/hostile-parent metadata, missing active artifact/backup/journal evidence, and deterministic parent/entry replacement are rejected without state rewrite, active recovery authority is retained for cleanup retry, iterative cleanup is globally bounded to 32 removable entries and 32 directory levels with preflight before mutation, and atomic replacement retains the prior target. Persisted manifests remain revalidated before transport or installer use, export, preflight, backup, or handoff. The current local result is 378 focused tests with six expected capability skips and 2,569 full-suite tests with 13 expected capability skips. Source-level evidence does not claim handle-based no-follow atomicity, signing, SmartScreen, Defender, Microsoft, release, or downloaded-candidate execution acceptance |

### 2026-09-02 Windows updater/recovery JSON parser containment

| Requirement | Implementation/evidence | Status |
|---|---|---|
| UPDATER-06 — bounded authority-bearing updater/recovery JSON must not escape parser failures | `windows_update_helper.py::_decode_json`; `windows_update_helper.py::_read_json`; `tests/unit/test_windows_update_helper.py::test_read_json_contains_real_parser_failures`; `test_read_json_classifies_utf8_json_and_root_failures`; `test_read_json_accepts_exact_byte_limit_with_multibyte_utf8`; `test_read_json_handles_multibyte_boundary_without_overread`; `test_json_decoder_does_not_swallow_process_control_exceptions`; `test_json_decoder_does_not_swallow_unexpected_programming_errors`; `test_frozen_core_guard_contains_real_pathological_state_json`; `test_pre_cutover_staging_parser_failure_refuses_state_reset`; `test_journal_entry_points_contain_pathological_json`; `test_application_state_callers_preserve_parser_error_classification`; `test_run_transaction_contains_pathological_child_reports`; `tests/unit/test_recovery_admin.py::test_recovery_doctor_contains_untrusted_startup_marker`; `test_packaged_core_startup_contains_real_pathological_state_json`; ADR-186 | Implemented and locally tested. Real stdlib `json.loads` huge-integer and deep-nesting failures, malformed/invalid UTF-8 inputs, non-dict roots, exact/over-limit and multibyte-boundary cases normalize or classify through the existing bounded `HelperError` vocabulary. Frozen packaged Core blocks before Core/vault startup; staging reset is refused; journal/state callers preserve `metadata_unreadable`; apply, diagnostics, and health report failures return the existing rollback code and restore logical vault/binary state; journal-failure and recovery-doctor projections remain fixed and sanitized. The reader still requests at most configured limit plus one sentinel byte. On Python 3.14.3/Windows, the focused updater/recovery/desktop command passed 263 tests with three expected platform-capability skips and the full suite passed 2,504 tests with 10 expected platform-capability skips; Ruff, mypy, and documentation checks passed. Local evidence is source/test coverage only; no parser depth/CPU guarantee, handle-based TOCTOU guarantee, hosted/exact-artifact/package, signing, SmartScreen, Defender, Microsoft, release, or candidate-execution acceptance is claimed |

### 2026-09-01 Windows startup/recovery containment and rollback preservation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| UPDATER-04 — frozen Windows Core must not start across unsafe persisted recovery state | `windows_update_helper.py::ensure_recovery_before_core`; bounded state/manifest validation; bundled-keyring signature verification; operation-owned signed manifest and artifact SHA-256/size evidence; reparse-safe directory-chain and plain-file checks; atomic `updates/startup-recovery.json`; packaged `--recovery-doctor`; `test_core_start_guard_blocks_unreadable_state_and_records_safe_diagnostic`; `test_core_start_guard_blocks_invalid_journal_and_keeps_core_down`; `test_core_start_guard_resets_pre_cutover_install_without_transaction`; `test_core_start_guard_rejects_unsigned_pre_cutover_manifest`; `test_core_start_guard_rejects_staged_artifact_digest_or_size_mismatch`; `test_core_start_guard_rejects_stale_staging_for_a_different_operation`; `test_core_start_guard_rejects_reparse_staging_paths`; `test_core_start_guard_rejects_reparse_state_parent`; `test_core_start_guard_rejects_reparse_transaction_parent`; `test_core_start_guard_does_not_reset_pre-cutover_install_without_valid_operation`; `test_core_start_guard_does_not_reset_valid_format_unknown_operation`; `test_core_start_guard_rejects_invalid_handoff_identity`; `test_core_start_guard_does_not_treat_pending_identity_as_unbound`; ADR-184 | Implemented and locally tested. Unreadable or invalid state/journal blocks Core before vault startup, leaves the original state/database untouched, and records only fixed allowlisted recovery facts. A valid pre-cutover `installing` state with a valid operation ID, operation-path-bound signed manifest, state-field match, signed artifact digest/size match, and no transaction/directory/identity evidence resets to an error state without touching the vault; missing/invalid operation, signature, staging, artifact, reparse-path, or active-handoff evidence still blocks. A pre-cutover abort persists an abort authority before rolled-back state and replay stays on the abort path. The health-check environment is scheduler suppression only and cannot bypass this guard. Only the packaged doctor's `startup_recovery` field is content-free; its existing operator path fields remain. Exact packaged and hosted evidence remain open |
| UPDATER-05 — rollback must preserve the user vault boundary and prevent stale SQLite sidecar replay | `windows_update_helper.py::_restore_database`; `test_power_loss_after_each_post_cutover_phase_replays_safely`; `test_rollback_removes_all_sqlite_sidecars_and_preserves_unrelated_user_files`; existing verified database backup and rollback-retry tests; ADR-184 | Implemented and locally tested. Replay after diagnostics/health process loss reaches commit; failed-health rollback restores the verified pre-update database, removes WAL/SHM/rollback-journal sidecars, and leaves unrelated user files intact. No release, live-dogfood, Defender, Microsoft, or N-1 credit is created |
| UPDATER-04 diagnostic containment — startup-recovery persistence and packaged consumption must remain bounded and non-authoritative | `_write_startup_recovery_diagnostic`; `startup_recovery_diagnostic`; `ensure_recovery_before_core`; packaged `--recovery-doctor`; packaged `--core`; `test_startup_diagnostic_writer_contains_atomic_write_failure`; `test_startup_recovery_diagnostic_is_bounded_for_untrusted_markers`; `test_startup_recovery_diagnostic_bounds_read_after_marker_grows`; `test_startup_recovery_diagnostic_rejects_reparse_marker_when_supported`; `test_core_start_guard_ignores_untrusted_existing_marker_without_escaping`; `test_core_start_guard_stays_blocked_when_diagnostic_marker_is_non_regular`; `test_core_start_guard_contains_marker_probe_race`; `test_recovery_doctor_contains_untrusted_startup_marker`; `test_packaged_core_startup_contains_diagnostic_write_failure`; ADR-185 | Implemented and locally tested. Marker writes use the existing atomic/reparse-safe path and suppress persistence failures; marker reads request at most 16 KiB plus one sentinel byte and accept at most 16 KiB, while missing markers remain absent and other untrusted markers produce only a bounded fixed projection. Focused updater/recovery/doctor tests passed 119 tests with one symlink-capability skip; the full suite passed 2,475 tests with 10 platform-capability skips. The startup guard remains fail-closed for unsafe state and the packaged doctor/startup surfaces do not expose raw marker content, paths, or tracebacks. Hosted, exact-packaged, signing, SmartScreen, Defender, Microsoft, release, and end-user dogfood evidence remain open |
### 2026-09-01 packaged Windows uninstall observation stabilization

| Requirement | Implementation/evidence | Status |
|---|---|---|
| WIN-CI-02 — packaged uninstall observation must cover the full bounded cleanup contract | `_schedule_windows_install_removal`; shared 300-attempt/100-millisecond/30-second constants; `smoke_packaged_first_run.py`; focused desktop runtime and packaged-first-run tests; full 2,403-test suite; ADR-183; protected-main CI run `33459729438` | Corrected and validated locally after exact protected main passed seven CI jobs and CodeQL but the Windows desktop smoke found the install directory at its former 15-second deadline. The product cleanup behavior remains 30 seconds; only the smoke observer uses that contract plus a five-second harness margin. Directory, startup, shortcut, Apps & Features, managed-client, credential, listener, and vault-preservation assertions remain mandatory. Repository-wide Ruff, mypy, documentation checks, and full pytest with nine expected platform skips pass. Reviewed merge and green exact protected-main CI/CodeQL are required before private candidate dispatch |

### 2026-09-01 protected-main Windows test coordination stabilization

| Requirement | Implementation/evidence | Status |
|---|---|---|
| WIN-CI-01 — hosted Windows heartbeat tests distinguish scheduler/worker coordination from product liveness | `tests/unit/test_import_operations.py`; `CANCEL_QUIESCE_SECONDS`; 20 repeated focused Python 3.12 runs; 57-test import-operations file; full 2,402-test suite; ADR-182; protected-main CI run `33454650627` | Corrected and validated locally after exact protected main passed CodeQL and all CI jobs except Windows Python. Test-only worker entry/cleanup uses the documented quiescence budget plus a five-second harness margin and fails early on worker exit. The original one- and two-second durable-heartbeat observation windows, exact byte-progress checks, terminal outcomes, and heartbeat-thread cleanup assertions remain unchanged. Repository-wide Ruff, mypy, documentation checks, and full pytest with nine expected platform skips pass. A reviewed merge and green exact protected-main CI/CodeQL are required before private candidate dispatch |

### 2026-08-31 exact protected-main replacement acceptance and dialog containment

| Requirement | Implementation/evidence | Status |
|---|---|---|
| WIN-GA-03 — verify one exact private replacement candidate through install, recovery, rollback, and uninstall | Protected-main SHA `e9cf314f7a3af1c55ab118f1a850a067b6447336`; CI `33442280360`; CodeQL `33442280066`; private workflow run `33444357750`; artifact `9777536856`; strict 18-file provenance/inventory/hash verification; network-disabled disposable Windows Sandbox | Passed as disposable exact-artifact engineering evidence. Current-user install, real Windows Credential Manager round trip, HKCU startup, shortcuts/registration, Core health, and loopback binding passed. Injected `binary_replaced` exit `86` resumed with exit `0` to `committed`; forced unhealthy update returned `2`, reached `rolled_back`, restored four exact components and the pre-mutation database; uninstall removed app/integration state and preserved the vault. No live dogfood, publication, release, signing, Microsoft, or literal beta.6 N-1 claim is created |
| WIN-AV-07 — separate exact host Defender observation from unavailable Sandbox AV | Host Defender custom scan with antivirus/real-time enabled and signature `1.457.430.0`; exact 18-file staged candidate before/after identity; Sandbox Defender capability receipt | Host scan passed with zero new detection/quarantine events and no file/hash/size loss. Windows Sandbox reported Defender unavailable, so its AV scan is explicitly `not_exercised_defender_unavailable`. This is not Microsoft reassessment, malware clearance, SmartScreen reputation, signing, allow-listing, or release acceptance |
| UPDATER-03 — windowed internal update-child failures must not block rollback on UI | `_run_silent_internal_mode`; diagnostics/apply-update/update-health dispatch; focused `test_internal_update_child_failure_returns_nonzero_without_escaping` and `test_internal_update_child_preserves_deliberate_process_exit`; ADR-181; repository-wide Ruff/mypy and full pytest | Implemented and validated locally after a disposable injected misconfiguration produced a PyInstaller traceback dialog while the helper still rolled back. Ordinary child exceptions now become exit `1` without escaping to the windowed bootloader, while deliberate `SystemExit` crash injection remains visible to the recovery transaction; the updater retains report validation, timeout, failure-state, and rollback authority. Full pytest passes 2,402 tests with nine expected platform skips. Merge, hosted CI, and a new exact packaged candidate remain required |
| UPDATER-02 — retain an honest N-1 boundary after a flagged public predecessor | Immutable public beta.6 incident; safe same-version beta.7 crash/rollback receipts; ADR-179/ADR-181 | Still open. The exact beta.7 helper and replacement bytes exercised transaction recovery without restoring, executing, or allow-listing the flagged beta.6 helper. A physical N-1 receipt still requires a vendor-cleared predecessor or separately reviewed safe fixture |

### 2026-08-31 exact protected-main beta.7 Windows candidate evidence

| Requirement | Implementation/evidence | Status |
|---|---|---|
| WIN-GA-01 — bind private Windows candidate evidence to exact protected main | Protected-main SHA `af3b6a15c2c10289bb89f62199b359041f2ea73d`; CI run `33404631149`; CodeQL run `33404630078`; clean isolated Python 3.12.10/PyInstaller 6.21.0 build; canonical and independent installed-component archive verification | Exact-source engineering evidence prepared. Canonical archive SHA-256 is `784c7372af44543855cd544e5a48303d3b96c621f26a7eb6d0f2ddface289d74`; main, MCP, recovery, and updater hashes are manifest-bound. This is not the release-candidate workflow/SLSA artifact and creates no tag, draft, publication, release, or live dogfood claim |
| WIN-GA-02 — exercise the private packaged candidate without live-state mutation | `scripts/smoke_desktop_artifact.py`; `scripts/smoke_packaged_recovery.py`; `scripts/smoke_packaged_first_run.py`; packaged credential acceptance | Passed in disposable state: desktop launch, recovery diagnostics/export-restore-purge/integrity, first run, browser handoff, MCP, restart, installed reopen, per-user startup, automatic-install support, interrupted cutover recovery, rollback/database restoration, shutdown, uninstall, and cleanup. The separate packaged credential acceptance completed a real isolated Windows Credential Manager set/get/delete round trip. No live dogfood state or user configuration was touched, and no clean fresh-Windows receipt is claimed |
| WIN-AV-06 — record current Defender observations without turning them into vendor clearance | Windows Defender custom scans with antivirus and real-time protection enabled at signature `1.457.423.0`; exact local archive and four manifest-bound executables; exact post-merge CI direct executable and archive | All scanned disposable copies remained present with zero target detections. Four content-free component submission manifests were prepared and held, not uploaded or submitted. This is a point-in-time local observation, not Microsoft reassessment, a malware/false-positive determination, SmartScreen reputation, signing, allow-listing, or release acceptance |
| UPDATER-02 — retain an honest N-1 boundary after a flagged public predecessor | Immutable public beta.6 incident record; ADR-179 | A physical beta.6-to-beta.7 N-1 run was not attempted because the flagged beta.6 helper must not be restored, executed, or excluded from antivirus. N-1 remains open until a vendor-cleared prior artifact or separately reviewed safe fixture can produce an exact receipt |

### 2026-08-31 private Windows Defender reassessment preparation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| WIN-AV-03 — prepare a private, content-free candidate security bundle without creating trust claims | `scripts/prepare_windows_security_submission.py`; `tests/unit/test_prepare_windows_security_submission.py`, including direct CLI execution with an unrelated shadow `scripts` package | Implemented locally: direct execution deterministically imports its sibling verifier; exact archive/package/direct-package/component/manifest/checksum/source-root/source-commit/version inputs are verified; output contains only bounded digest/size/provenance and hold metadata, with no candidate bytes, paths, credentials, logs, or user context. The preparer does not execute, upload, publish, contact Microsoft, or claim signing/malware clearance |
| WIN-AV-04 — bind all verification phases and output ownership fail closed | operation-wide input identity/ancestry revalidation through the final output write; stable measurement of every component; exclusive-handle identity capture before the first byte; final exact-byte re-read of both outputs; newly owned output creation with failure retention; adversarial phase-swap, post-validation mutation, final-write and in-place output mutation, root-swap, existing-output, identity-change, partial-write, hardlink, and redirection tests | Implemented locally: source-root and component TOCTOU swaps, manifest-to-measurement disagreement, output replacement/redirection, and in-place mutation of either final output are rejected. Failed bounded content-free output is retained rather than deleted through a raceable pathname. ADR-179 records the later exact private artifact; Microsoft Defender submission/reassessment and release acceptance remain open |
| WIN-AV-05 — reject hostile manifest numbers and archive paths | `scripts/installed_component_manifest.py`; focused strict-count, NaN, exponent-overflow, duplicate-key, symlink, traversal, drive-qualified, and NUL-truncated ZIP tests | Implemented locally: canonical parsing rejects duplicate/non-finite/non-integer structural values and ZIP validation rejects Windows drive-qualified and NUL-truncated members. This is source/test evidence only; no Defender or malware result is inferred |

### 2026-08-31 focused updater/recovery trust hardening

| Requirement | Implementation/evidence | Status |
|---|---|---|
| UPDATER-01 — a verified staged candidate and detached recovery path must not silently change before use | `updater.py` install-time manifest/artifact identity and checksum checks; `PlatformInstaller` opened-archive recheck and state handoff binding; version-2 `UpdateJournal` recovery-helper binding; `windows_update_helper.py` current/pending/completed state/journal reconciliation plus pre-launch/pre-subprocess verification; `tests/unit/test_updater.py` and `tests/unit/test_windows_update_helper.py` adversarial artifact swap, replacement/parent/database/terminal journal forgery, terminal-phase skip, transition crash sides, incomplete cutover resume, missing binding, interrupted preparation, reparse/hardlink, and replacement checks | Implemented locally as source-level hardening. The persisted signed manifest, staged ZIP, journal handoff and rollback authority, copied recovery helper, replacement, installed application, and rollback sources fail closed when their required identity or SHA-256/size binding is absent or changes. Authority-changing parent/database updates reconcile either cross-file crash side; terminal cleanup requires a state-first terminal marker; an unbound pre-cutover preparation clears its operation so the candidate can retry; a `cutover_started` resume re-applies and verifies all packaged components. Focused tests pass locally. Literal cross-file atomicity, recursive cleanup races, the final portable validation-to-process-creation interval, exact packaged/live behavior, and release acceptance remain open |

### 2026-08-30 cross-client dogfood hardening candidate

| Requirement | Implementation/evidence | Status |
|---|---|---|
| HD-01 — workspace capture must cross the historical single-page ceiling without a second authority | `capture_runtime.py::_workspace_state_reader`; `experimental_local_git_workspace_connector.py` v1 cursor/catalog paging; `tests/unit/test_local_git_workspace_connector.py` snapshot, restart, replay, incremental, mass-deletion, mutation-reset, same-length-content, coordinator-reconciliation, and discovery-cap cases; ADR-176 | Implemented locally as bounded source behavior: Core reconstructs opaque current/deleted item state; cursors remain metadata-only; pages contain at most 128 items; every page is content-bound and mutation-safe. Discovery stops at 16,384 files, while the shared 100-page/10,000-event coordinator makes 10,000 the effective completed-run ceiling. Catalogs are deliberately rescanned per page, so large full scans remain approximately quadratic. No private-workspace, packaged, or throughput acceptance is claimed |
| HD-02 — Hermes setup must select one native profile and preserve unrelated configuration | `hermes_config.py`; Hermes flags in `desktop.py`, `desktop_setup.py`, and `wizard.py`; `tests/unit/test_hermes_config.py`; headless option regression in `tests/unit/test_headless_local_source_setup.py`; ADR-177 | Implemented locally: exact explicit/active-profile resolution for Windows and POSIX layouts; marker-owned YAML and exact-command allowlist edits; repeat no-op; rollback-capable two-file transaction; disconnect preserves unrelated bytes/approvals. Malformed or unsupported YAML fails before mutation. Setup does not start/restart Hermes. Dashboard/uninstall disconnect orchestration, packaged/live profile acceptance, and macOS support are not claimed |
| HD-03 — Hermes retrieval, capture, and explicit mutation must not share authority | `hermes_read` MCP profile; `hermes_hook.py`; `lifecycle_runtime.py`; `mcp_adapter.py`; `tests/unit/test_hermes_hook.py`, `tests/unit/test_hermes_config.py`; ADR-177 | Implemented locally: pre-generation bootstrap uses a principal scoped exactly `context:read`; optional post-generation lifecycle capture uses a different principal scoped exactly `context:capture`; neither receives propose/witness authority. Both require OS credential storage, and missing/unknown/fallback metadata is rejected before file access. Hook input/output is bounded, secret-shaped content is a no-op/refusal, and returned context is framed as untrusted data |
| HD-04 — Codex and Hermes must share Core truth across ordinary capture, explicit overrides, and restart | `tests/integration/test_cross_client_memory_acceptance.py` | Implemented as one disposable in-process Core/TestClient journey: five distinct principals; 13 ordinary events plus idempotent replays; exact user/assistant/tool/imported provenance; cross-client formation/reinforcement into one canonical preference; retrieval by both clients; two Core restarts; explicit correction and forget through the separate Codex writer; read/capture mutation denials; operational-secret refusal and absence checks over SQLite/WAL, audit, bootstrap/model context, and encrypted/decrypted export. This is synthetic HTTP composition, not a packaged or real Codex/Hermes/provider conversation |
| HD-05 — generated Hermes configuration must match the installed host schema without touching live state | fresh disposable `HERMES_HOME`; source `configure_hermes`; installed `hermes mcp list` and `hermes hooks list` | Local smoke passed: the installed Hermes CLI parsed one enabled four-tool MCP server and two exact allowlisted `pre_llm_call`/`post_llm_call` hooks from the generated temporary profile. The live Hermes home, live ATC vault, and private context were not used. This grants source/schema compatibility evidence only |
| HD-06 — material hardening changes must pass adjacent and repository-wide validation | repository-wide Ruff; mypy over `packages/allthecontext/src`; combined focused surface; Packet H admission/truth/retrieval surface; full pytest | Passed locally: Ruff clean; mypy clean over 107 source files; changed/adjacent focused tests 153 passed and three expected platform skips; Packet H migration 61 passed; full pytest 2,349 passed and nine expected platform skips, with two pre-existing Starlette deprecation warnings. Hosted CI remains uncredited until bound to the pushed candidate SHA |

### 2026-08-30 replacement workflow contract re-review remediation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Private replacement workflow must not accept parser or shell semantic bypasses | `.github/workflows/replacement-candidate.yml`; code-owned `EXPECTED_WORKFLOW_SHA256` and exact producer/verifier/consumer contract in `tests/unit/test_replacement_candidate_workflow.py`; 35 focused contract tests | Implemented locally: the complete reviewed workflow is checked as exact UTF-8 bytes, so comments, Unicode line separators, YAML indirection/tags, duplicate keys or steps, quoted control keys, alternate inputs/paths, scalar tricks, extra statements, detached/post-verifier mutators, and noncanonical verifier scripts fail closed. Readable assertions retain the manual Windows-only, pre-checkout SHA binding, least-permission, pinned-action, no-publication, exact-handoff, and producer-before-verifier-before-upload requirements. The workflow explicitly propagates the native verifier exit code. No beta.7 candidate, security scan, Microsoft reassessment, client, or release acceptance is claimed |
| Private candidate handoff guards must be executable PowerShell and failed pre-upload runs must not receive artifact credit | `.github/workflows/replacement-candidate.yml`; readable inventory-guard assertion in `tests/unit/test_replacement_candidate_workflow.py`; failed exact-main run `33417852210`; ADR-180 | Corrected locally: the pre-existing-inventory check composes two parenthesized `Test-Path` calls with `-or`, while the code-owned complete-workflow digest continues to fail closed on any byte drift. Run `33417852210` passed build and independent verification but failed before content hygiene, final rehash, and upload, so it produced no downloadable candidate and grants no execution, Defender, release, Microsoft, or dogfood acceptance. A fresh exact protected-main dispatch is required after merge and hosted validation |

### 2026-08-29 public beta.6 and private beta.7 replacement-slot reconciliation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Public beta and replacement-source identity remain distinct | Immutable `0.1.0-beta.6` public release; source metadata `0.1.0-beta.7`; `README.md`; `docs/operations/RELEASES.md`; ADR-168/ADR-179 | `0.1.0-beta.6` remains the current downloadable public prerelease and its Defender incident remains unresolved. A private exact-main `0.1.0-beta.7` engineering candidate has now been built, exercised in disposable state, and observed by local Defender scans, but it has not been submitted, approved, published, tagged, uploaded, or released |
| Private replacement workflow remains evidence-bounded | Replacement-candidate workflow, exact artifact allowlist, approval gate, `docs/operations/RELEASES.md`, ADR-167/ADR-168 | Artifact-only and exact-allowlist; approval-gated; not publication, execution, or AV evidence. Future beta.7+ acceptance requires exact candidate-bound Microsoft closed no-malware reassessment evidence, and no such evidence exists |
| Codex pre-generation retrieval and evidence boundary | `codex_hook.py`; `client_config.py`; Codex read/capture/explicit profiles; focused hook and stdio tests; `docs/protocols/MCP_API.md` | Separate `context:read` principal, exact `${prompt}` `mcp_tool` templating, optional `required=false` read/capture/explicit servers, untrusted `additionalContext` framing, and fail-empty Core outage behavior are source-level contracts. Current stdio coverage is transport-only; native Codex trust and exact packaged/live host acceptance remain open |
### 2026-08-30 Packet A L0 specification freeze

| Requirement | Implementation/evidence | Status |
|---|---|---|
| PACKET-A-01 — single canonical, non-displacing research specification | [`bench/memory_reliability_spec.json`](../bench/memory_reliability_spec.json), [`docs/research/ATC_PACKET_A_SPECIFICATION_FREEZE_2026-08-30.md`](research/ATC_PACKET_A_SPECIFICATION_FREEZE_2026-08-30.md), [`bench/validate_memory_reliability_spec.py`](../bench/validate_memory_reliability_spec.py), `tests/unit/test_memory_reliability_spec.py` | L0 specification-only. The active product frontier and product DAG remain blocking; no frontier advancement, product prerequisite, production schema/runtime change, production data collection, external access, execution, promotion, or L2/L3 Packet A claim is authorized |
| PACKET-A-02 — pre-execution hard-safety exposure and denominator integrity | `packet_a.hard_safety_exposure_contract`, `packet_a.opportunity_contract`, exact response-status allowlist, operational estimand fields, and fail-closed mutation probes | Frozen contract requires mechanism-independent pre-execution `S_h` coverage with at least one opportunity per rule/arm; absent, indeterminate, or unexercised exposure cannot support a zero-failure claim, and every non-credit response remains visible |
| PACKET-A-03 — reproducibility and future source identity | `packet_a.fixture_repository_contract`, `packet_a.confirmatory_design.source_state_binding`, `packet_a.power_simulation` | Future manifest must bind repository ID, immutable commit/ref, complete file inventory, SHA-256 digest, and independently emitted derived N. Provisional 384 is non-authoritative; 15% nonrecoverable infrastructure loss is a frozen power input. No fixture manifest or execution exists |
| PACKET-A-04 — code-owned semantic freeze and receipt integrity | `bench/validate_memory_reliability_spec.py`, `packet_a.content_binding`, `packet_a.future_receipt_requirements`, `tests/unit/test_memory_reliability_spec.py` | Complete canonical JSON digest is independently code-owned; self-digest recomputation cannot authorize drift. Duplicate keys, non-finite values, unknown/missing structure, narrative/proposal/source drift, and missing future receipts fail closed. The validator is a specification-integrity gate, not benchmark evidence |
| PACKET-A-05 — corrected opportunity and hard-safety semantics | `packet_a.opportunity_contract`, `packet_a.hard_safety_exposure_contract`, `packet_a.caos_contract`, `packet_a.cell_status_contract` | `E_w` is a pre-execution mechanism-independent complete response-status partition; non-abstention is exactly `count(SUPPORTED response statuses) / E_w`. The rule×arm universe is pre-execution; `NOT_APPLICABLE` is not exposure; every applicable rule/arm has an exposed-opportunity floor and outcome report |
| PACKET-A-06 — proposal erratum and non-authoritative planning N | `docs/research/POST_BETA_CONTINUITY_AND_MEMORY_PROPOSAL_2026-08-29.md` Section 6, `packet_a.content_binding.proposal_correction` | `PACKET-A-ERRATUM-2026-08-30` supersedes the earlier non-abstention subtraction formula and removes any requirement to reproduce 384. Final N remains independently simulation-derived after future fixture/reproducibility gates |
| PACKET-A-07 — third semantic remediation and exact cross-field bindings | `packet_a.confirmatory_design`, `packet_a.task_manifest_contract`, `packet_a.failure_and_replacement_contract`, typed `packet_a.estimands`, `packet_a.hard_safety_exposure_contract`, `packet_a.caos_contract`, and validator regressions | 96 balanced base cells are separated from the provisional minimum 384; final N is `96 * ceil(max(N_power, 384) / 96)` with repetitions `N_final / 96`. `E_w`/coverage/`E_eff`, five-state `S_h`, task/source receipts, replacement/last-valid-state rules, typed comparison vocabulary, and root CAOS equivalence are exact and fail closed. No execution, integration, product, client, updater, workflow, or promotion scope is added |
| PACKET-A-08 — bounded ingestion and closed trust/receipt topology | `bench/validate_memory_reliability_spec.py` bounded reader/parser, `packet_a.m1_contract`, `packet_a.secret_refusal.non_reflection`, `packet_a.hard_safety_exposure_contract.status_schema.safety_rate_mapping`, and targeted unit mutations | JSON, narrative, and provenance inputs are bounded before parsing or hashing; duplicate, non-finite, malformed, depth, node, string, numeric, and read-identity failures reject content-free. M1 freezes exact lifecycle issuers/witnesses, configured same-device user evidence grants, tentative defaults, non-relabeling Relay/provider paths, S0–S3 sensitivity, ACL filtering, complete task/source/reserve/last-valid/outcome schemas, and mandatory episode bindings. Secret refusal forbids unkeyed content-derived verifiers and requires SQLite/WAL/freelist/FTS/diagnostic/export/restore scans. Scheduler and Continuity Debt are dimensionless relative contrasts; manifest repetitions derive from final N; all five S_h statuses map exactly to safety numerator/denominator/exclusion/disposition. L0 specification-only; no execution or product/runtime scope is added |
| PACKET-A-09 — unified bounded parsing for embedded narrative JSON | `_parse_bounded_json_bytes`, `_validate_narrative`, content-free `_require_keys`/duplicate/non-finite diagnostics, and focused regression tests | The fenced narrative binding uses the same byte/depth/node/string/numeric/duplicate/non-finite/malformed policy as the top-level JSON document. Exact expected keys remain enforced without exposing unknown or secret-like key names; discarded 1,100-level values and rebound narrative/code-owned digests fail closed. This remediation is included in the final exact chain through `605330ce5564346a666dfa08418e6d87badad5c3`, which two fresh reviewers independently approved |
| PACKET-A-10 — recursive structure, alias, encoding, and power-policy closure | Code-owned recursive structure digest, iterative alias detection, strict UTF-8 narrative boundary, content-free public exception normalization, `packet_a.power_simulation.computation_method`, `packet_a.power_simulation.primary_contrast_methods`, `packet_a.power_simulation.interim_and_stopping_policy`, and focused structure/policy regressions | Unknown/missing/wrong-type/extra/reordered/identity-mutated nested containers fail before semantic field access; shared references are distinct from cycles; malformed causes do not disclose candidate content. The two primary contrasts have separate frozen binary-CAOS and bounded-five-level-utility power methods; the future computation and stopping policy are specified but not executed. No derived N, manifest, result, experiment, product/runtime change, or Packet A acceptance claim exists |
| PACKET-A-11 — independent authority-source and safe-path closure | Validator-owned `EXPECTED_CONTRACT_SOURCE_SHA256`, normalized `bench/packet_a_contract.py` provenance/content/narrative bindings, exact concrete-path checks, bounded identity-checked reads, exception-graph regressions, and hostile path probes | Contract-source drift fails without coordinated rebinding; virtual/path-like/subclassed inputs, links/reparse points, special files, hard links, root escapes, and identity swaps fail closed; JSON, UTF-8, encoding, and missing-file failures expose neither raw content nor chained path/exception objects. No experiment, manifest, result, private data, product/runtime change, or Packet A acceptance claim exists |
| PACKET-A-12 — executable deterministic power-method reference | [`bench/packet_a_power_reference.py`](../bench/packet_a_power_reference.py), `packet_a.power_simulation.computation_method.reference_method_contract`, source/provenance bindings, golden counter vectors, utility-axis/means/effect regressions, exact contrast and Holm tests | The source-bound reference implements fixed SHA-256 counter serialization and uniforms, all 96 cell mappings, control-row/alternative-column utility sampling, exact binary and studentized utility methods, fixed bootstrap/permutation counts and interpolation, loss/missing/invalid handling, Holm conjunctions, 100,000-replicate candidate estimation, and no-result selection. It is executable reference code only; no power run, manifest, result, derived N, experiment, product/runtime change, or Packet A acceptance claim exists |
| PACKET-A-13 — final infrastructure-loss and review closure | `bench/packet_a_power_reference.py`, `bench/memory_reliability_spec.json`, narrative/proposal bindings, 39 focused tests, exact final authored commit `605330ce5564346a666dfa08418e6d87badad5c3` | Independently diagnosed infrastructure-loss pairs remain in the eligible-opportunity ledger and `E_w`, are reported through effective N/`E_eff`, and are excluded from efficacy estimators, tests, and bootstrap samples rather than fabricated as zero pairs. The power conjunction is explicitly monotone and attainable. Two fresh reviewers independently approved the exact final commit and parent. This closes the L0 specification review only; no benchmark/provider run, production data, result, promotion, or frontier advancement is claimed |

### 2026-08-29 merged Continuous Capture milestone reconciliation

| Milestone requirement | Implementation/evidence | Status |
|---|---|---|
| ATC-CC-M01 — Core continuous-capture authorization and ingestion | `security.py` narrow `context:capture` scope; strict `CaptureEventRequest`; `POST /v1/lifecycle/events`; `CoreCaptureService`; migration 018; `tests/integration/test_client_capture_core.py` | Merged in PR #95. The request cannot self-assert witness, provenance, sensitivity, ACL, availability, or authority; Core derives them from the durable capture principal. Relay has no authority or fallback role |
| ATC-CC-M02 — observation formation and reconciliation | `client_capture.py`; `extract_live_user_claim`; `LIVE_USER_EVIDENCE`; existing candidate, slot-reconciliation, version, provenance, and conflict machinery; formation/replay tests | Merged for a narrow deterministic first-person claim set. Equivalent evidence reinforces/deduplicates and justified slot changes supersede through Core. Assistant, tool, and imported observations cannot independently form user truth; this is not a general semantic extractor |
| ATC-CC-M03 — Claude Code lifecycle capture and retrieval | distinct `claude_code_read` and `claude_code_capture` profiles; `claude_code_hook.py`; managed `UserPromptSubmit`/`Stop` hooks; adapter-to-Core and focused hook tests | Merged at source level. Retrieval and capture use separate principals. Claude Code supplies no stable turn ID, so session-only callbacks remain bounded, unpaired, and at-least-once with no prompt/session deduplication or exactly-once claim. Exact packaged/live acceptance remains open |
| ATC-CC-M04 — Codex lifecycle capture and retrieval | distinct read and capture MCP registrations; `codex_hook.py`; managed user-prompt/stop hooks; focused setup/hook tests | Merged at source level with bounded prompt/response capture and pre-generation bootstrap. Read, capture, and explicit mutation authority remain distinct. Exact packaged/live acceptance remains open |
| ATC-CC-M05 — one-time setup/configuration for both clients | `client_config.py`, `claude_code_config.py`, `desktop_setup.py`, `wizard.py`; transactional configure/repair/opt-out/disconnect tests | Merged false-by-default. One explicit setup choice installs managed surfaces; repeat/repair preserves unrelated settings, and successful opt-out/disconnect removes managed surfaces and retires associated authority |
| ATC-CC-M06 — explicit remember/correct/forget controls | dedicated Core routes and exact `context:propose` plus `witness:explicit_user_statement` principal; Claude Code and Codex explicit profiles/skills; idempotency, confirmation, correction, and reversible-forget tests | Merged as a separately selected, higher-authority path. Ordinary prompts, model/tool/provider output, imports, and lifecycle capture are not reclassified as explicit durable-memory commands |
| ATC-CC-M07 — sensitive-context and operational-secret boundaries | Core sensitivity/ACL assignment; pre-ledger secret refusal; export/log absence tests; `LocalSecretReferenceVault` OS-keyring-only raw-value boundary | Merged. Sensitive personal context may be local memory with Core ACLs; raw credentials never enter ordinary memory, retrieval, replication, export, logs, audit text, or model context. The optional raw-secret facility fails closed without an OS credential backend |
| ATC-CC-M08 — validation, documentation, decisions, and traceability | PR #95 twelve-check result; protected-main CI run `33254733214`; CodeQL run `33254733031`; `STATUS.md`, `DECISIONS.md`, this matrix, and the zero-friction boundary documents | Exact-SHA hosted validation passed at `29b3a19113e498a73c205d12ffff41faed02baa0`. Fresh locked Python 3.12.10 local validation passed Ruff, mypy over 105 source files, the documentation checker and eight documentation contract tests, 110 focused Continuous Capture tests, and full pytest with 2,114 passed, nine platform skips, and two deprecation warnings. Exact artifact/live/release acceptance is not implied |

### 2026-08-29 Windows installed-component provenance incident

| Requirement | Implementation/evidence | Status |
|---|---|---|
| WIN-AV-01 — endpoint-protection findings are not bypassed on outer-artifact provenance alone | Defender events 1116/1117; restored-helper Authenticode and SHA-256 inspection; beta.6 public archive checksum and release-workflow inspection; `docs/KNOWN_ISSUES.md`; ADR-166/ADR-167 | Open for beta.6: the outer archive matches its published digest and CI provenance, but that immutable release did not publish component-level bindings. The new future-candidate manifest does not retroactively bind, clear, restore, execute, or allow-list the flagged helper, and no malware or false-positive claim is credited |
| WIN-AV-02 — installed Windows executables are individually identifiable and reassessed | `release/installed-component-manifest.schema.json`; `scripts/installed_component_manifest.py`; `scripts/package_desktop.py`; `.github/workflows/release-candidate.yml`; exact beta.7 evidence in WIN-GA-01/WIN-AV-06 | Exact protected-main beta.7 local component identities were built, independently verified, and scanned as disposable copies with zero target detections. Microsoft reassessment remains open, so the beta.6 incident and recommendation gate do not close |

### 2026-08-27 opt-in client Continuous Capture

| Requirement | Implementation/evidence | Status |
|---|---|---|
| CC-01 — false-default automatic Claude Code and Codex lifecycle capture | `client_config.py`, `claude_code_config.py`, `desktop_setup.py`, `wizard.py`, `codex_hook.py`, `claude_code_hook.py`; focused setup/hook tests | Implemented locally: one setup opt-in installs exact managed `UserPromptSubmit` and `Stop` hooks. Read, capture, and optional explicit mutation use separate least-privilege principals. Claude read and capture profiles expose only their corresponding entrypoints; Claude's missing stable turn identity is treated as unpaired bounded evidence with no prompt/session deduplication or exactly-once claim. Ordinary opted-in turns require no ATC command or per-turn confirmation. Reconfiguration is idempotent, preserves unrelated configuration, rolls back multi-file writes, and retires omitted managed authority after a successful opt-out. Dashboard disconnect removes every managed integration surface and revokes read/capture/explicit principals; capture-principal revocation atomically revokes its source, abandons active runs, and clears pending checkpoint state. Default setup remains capture-off |
| CC-02 — Core-authoritative capture contract | `lifecycle_contract.py`, `CaptureEventRequest`; `POST /v1/lifecycle/events`; `CoreCaptureService`; migration 018; lifecycle runtime, hook, and `test_client_capture_core.py` tests | Implemented locally: one shared 16,384-character/65,536-byte content bound and 131,072-byte body bound are applied at provider schemas, runtime, client, and Core HTTP ingress so providers cannot knowingly send Core-rejected lifecycle data. Strict extra-forbid bounded flat requests use UUIDv4 idempotency and require `context:capture`. Core derives source, witness, provenance, sensitivity, local availability, ACL, and disposition from the authenticated durable principal. Raw turns are tentative local observations; retries replay where a stable identity exists, conflicting reuse fails closed, and no Relay path exists. The candidate/event index remains unique for registered-source and raw lifecycle observations; only Core-owned formation projections may share their originating event |
| CC-03 — narrow user formation and retrieval usefulness | `extract_live_user_claim`; `LIVE_USER_EVIDENCE`; existing storage reconciliation/retrieval; integrated adapter-to-Core test | Implemented locally for high-confidence first-person interaction preference, name, location, health, current project, goal, and workflow claims. Repeats reinforce/deduplicate and changed slot values supersede. Assistant/tool/imported content remains observation-only. This is not a general semantic extractor or live-client acceptance |
| CC-04 — personal sensitivity and operational-secret separation | Core sensitivity/ACL assignment; refusal receipts; `LocalSecretReferenceVault`; focused ACL, export-exclusion, and secret-reference tests | Implemented locally: sensitive and highly-sensitive formed personal claims remain local and are available only to authenticated, non-denied `context:read` principals; raw sensitive lifecycle evidence stays capture-principal-bound. Operational credential values are refused before the capture/observation ledger and excluded from ordinary retrieval/replication/export/log/audit/model surfaces. Optional raw values require an opaque OS-keyring reference and plaintext fallback fails closed; automatic credential routing to the vault is not claimed |

The detailed evidence above is focused and synthetic. PR #95 and protected-main
CI exercised full pytest plus the configured lint, type, documentation,
security, dashboard, and desktop-artifact jobs at the exact merged SHA. Exact
packaged installation, live/private client journeys, provider support, release
acceptance, and macOS support remain open.

### 2026-08-25 Milestone 5 graph foundation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| ZF-013 — bounded typed project graph | project_graph.py; tests/unit/test_project_graph.py | Implemented locally as an ephemeral single-project projection over caller-authorized temporal relation evidence. Six explicit/structural families, deterministic revision/receipts, cycle rejection, fan-out/node/edge/input caps, and bounded one-/two-hop expansion preserve direct provenance/dependency lineage. No persistence, prose parsing, model inference, runtime/API/UI wiring, provider/client, private-data, package, release, or hosted acceptance claim |
| ZF-013 — independent adversarial graph safety matrix and reusable oracle | `bench/zf013_graph_adversarial_fixtures.json`, `bench/zf013_graph_adversarial.py`, `tests/unit/test_zf013_graph_adversarial.py`, `tests/unit/test_project_graph_adversarial_conformance.py`, `docs/research/ATC_ZF013_GRAPH_ADVERSARIAL_ORACLE.md` | Independent sanitized contract complete locally: 14 cases and six exact observable dimensions cover authorization-first noninterference, cross-project isolation, ambiguity abstention, correction/supersession, `as_of`, delete/purge closure, stale dependencies, illegal topology, bounded expansion, deterministic rebuild, and untrusted-text inertness. Seven separate implementation-facing tests map every frozen case onto the actual typed graph; the standalone oracle itself remains product-import-free |

### 2026-08-25 integrated Milestones 1–3 (current checkout)

| Requirement | Implementation/evidence | Status |
|---|---|---|
| ZF-007 — explicit Core-owned Continuous Context scheduling and controls | `capture_scheduler.py`, Core admin scheduler routes, `desktop_setup.py`, Sources dashboard; `tests/unit/test_capture_scheduler_productization.py`, `tests/unit/test_scheduled_packet_f_local_source_acceptance.py`, `tests/unit/test_desktop_setup.py`, `tests/unit/test_packaged_local_source_setup.py`, dashboard API/DOM tests | Implemented locally: scheduler is disabled by default; dispatch requires the process gate, durable enablement, and no update-health override. Optional first-run workspace setup launches Core with the process gate, re-verifies installation identity for each scheduler request, and invokes the authenticated route with an immediately revoked one-time administrator; success requires valid, durable, dispatch-enabled, running worker state. Ambiguous activation is reconciled, and rollback occurs only after a positively observed disabled prestate. Authenticated Sources controls retain bounded connect, enable, pause/resume, run-now, revoke, and automatic-sync state. The clean-vault acceptance journey starts the real worker, persists and automatically resumes one retry across restart, and performs later incremental work without a dashboard. No exact packaged/live provider acceptance is claimed |
| ZF-008 — stable local workspace identity and authorization | `capture_runtime.py`, `desktop_setup.py`, `wizard.py`, `desktop.py`, `POST /v1/admin/capture/workspaces/authorize`; `tests/unit/test_capture_runtime.py`, `tests/unit/test_scheduled_packet_f_local_source_acceptance.py`, `tests/unit/test_packaged_local_source_setup.py`, `tests/unit/test_wizard_local_source.py`, `tests/unit/test_headless_local_source_setup.py` | Implemented locally: the Core authorization primitive requires an absolute root and explicit local-only acknowledgement and creates one disabled identity-bound workspace source. Optional first-run setup now composes that primitive into a usable local path: blank remains no-op, root/ack mismatches fail before vault mutation, earlier setup failures leave no workspace authorization, a new disabled source is enabled, same-root repeat is idempotent, paused/degraded state is preserved, a second root is refused, and scheduler activation is verified without a dashboard or direct cycle call. Root and source content stay out of progress, warnings, failures, completion, and the new workspace-specific headless fields; the established disposable smoke report remains governed by ADR-056. The worker acceptance separately proves initial snapshot, later update/deletion, and restart-stable public truth/retrieval. Exact packaged-artifact and live/private-workspace acceptance remain open; no provider support is claimed |
| ZF-009 — worker-backed pre-generation lifecycle context | `experimental_reference_host.py`; `experimental_reference_host_lifecycle.py`; `tests/unit/test_packet_g_worker_acceptance.py` | Implemented locally as developer acceptance: after one local authorization, the real scheduler worker produces public records without a dashboard or direct cycle call; a durable authenticated reader compiles only current provenance-backed records into capability-qualified references delivered before generation. After Core restart, the principal is reauthenticated from Core-owned state and the caller-owned L2 checkpoint is restored; a worker-driven update/delete is reflected in the next compile with stable updated identity, withdrawn identity excluded, unique current truth, and advanced checkpoint sequence. This closes the local manual-cycle composition gap, not a supported client, product checkpoint persistence, ZF-009 product exit, packaged/live support, Phase 2, release, or macOS support |
| ZF-010 — worker-backed direct-user formation continuity | `experimental_reference_host_formation.py`; `tests/unit/test_zf010_worker_continuity_acceptance.py`; worker/source fixtures reused from the ZF-007/ZF-009 journeys | Implemented locally as developer acceptance: the real worker creates public source truth without a dashboard or direct cycle call; separate durable reader and explicit-user-statement witness principals compile and form one declared interaction preference. Core restart reauthenticates both principals and scopes while the caller restores the typed host checkpoint. Correction keeps the record identity, forget makes it deleted and removes both values from retrieval, worker-created source truth stays current, direct-user envelopes stay content-free, and the resumed checkpoint advances. This closes the manual-cycle local composition gap, not automatic capture from a supported client, product checkpoint persistence, ZF-010 product exit, complete Packet H, packaged/live support, Phase 2, release, or macOS support |
| ZF-011 — stable project identity and discovery | `project_continuity.py`, `project_runtime.py`; `tests/unit/test_project_continuity.py`, `tests/unit/test_project_runtime.py` | Implemented locally as a bounded Core-derived projection: opaque project IDs, exact project scopes, one-anchor provider lineage, resolved/unresolved/ambiguous outcomes, cross-project isolation, and abstention on ambiguity. Graph discovery and learned assignment remain outside this milestone |
| ZF-012 — Project Context Capsule compiler | `project_continuity.py`, `project_runtime.py`; authenticated bootstrap plus optional admin routes; `tests/unit/test_project_continuity.py`, `tests/unit/test_project_runtime.py`, `tests/integration/test_mcp_stdio.py` | Implemented locally: current/lifecycle-eligible evidence is filtered before selection; items carry authority and provenance; default 12,000-character/32-item budgets report exact omissions and truncation; `optimized_rebuild` equals the full rebuild oracle after restart/reordering; an authorized client entering the sole or uniquely named resolved project receives the derived capsule automatically without opening ATC; ambiguity abstains |
| ZF-013 — project graph in Memory Lab | `packages/allthecontext/src/allthecontext/project_graph.py`, `bench/zf013_project_graph_contract.json`, `bench/zf013_project_graph_fixtures.json`, `bench/zf013_project_graph_benchmark.py`, `tests/unit/test_project_graph.py`, `tests/unit/test_project_graph_adversarial_conformance.py`, `tests/unit/test_zf013_project_graph_benchmark.py`, `docs/research/ZF013_PROJECT_GRAPH_EVALUATION.md` | Ephemeral candidate and harness self-test only: the frozen sanitized comparison uses a stdlib lexical control, checkout-local production `LexicalV3` over fixture-supplied current/project-eligible IDs, deterministic capsules, and the actual typed graph for one-/two-hop expansion plus six synthetic integration-hypothesis ablations. Local output distinguishes the exercised lexical ranker from the unexercised full RetrievalEngine/Core policy façade, uses `harness_self_test_passed`/`harness_self_test_failed`, and validates finite decisions fail-closed. No graph store, Core/retrieval/MCP wiring, learned relation, promotion evidence, or live usefulness claim is credited; ZF-013 production promotion remains open |
| ZF-014 — optional project inspection | `ProjectContinuity.tsx`, dashboard project API/types and tests | Implemented locally as a bounded read-only Project Continuity dashboard: resolved projects, current capsule sections, item/character budgets, and omission accounting are rendered; unresolved/ambiguous projects are excluded. No force-directed graph or graph acceptance is claimed |
| A-11 — platform and provider scope | `ROADMAP_TO_V1.md`; provider importer/parser tests and current Milestone 3 provider/runtime tests | Supported source/package targets for this checkout are Windows and supported Linux. macOS source, tests, and historical preflight/packaging code remain retained for portability and maintenance only; macOS is unsupported and creates no package, CI, release, provider/client, acceptance, or support claim. ChatGPT, Claude, and Grok remain parser/source targets in code and synthetic tests only; no live provider acceptance is claimed |
| 529 — applied/current policy before time/relevance | `EligibleRecordSelector`, temporal sidecar, retrieval policy/lifecycle tests | Implemented locally: authorization and current/applied lifecycle eligibility precede temporal resolution and relevance; staged, tentative, ignored, deleted, and purged content cannot become ranked current context |
| 530 — current and `as_of` retrieval | Retrieval V3 current/temporal API and focused current-worktree retrieval tests | Implemented locally: current and offset-aware `as_of` paths remain authorization-first with exact post-policy catalog totals; bounded bootstrap accounting is separate. No historical three-platform or hosted result is reasserted |
| 532 — weighted bounded FTS5 | `lexical_v3.py`, retrieval contracts, bootstrap composition tests | Implemented locally: catalog search is exact over the post-policy set within the 50,000-authorized-ID hard cap; bounded bootstrap uses its 100-record evidence pool, with content-only coverage and metadata-only exclusion |
| 533 — task admissibility | `admissibility.py`, `content_evidence.py`, `lexical_v3.py`; retrieval precision/bootstrap tests | Implemented locally: every nonempty multi-term direct request retains the strict `0.75` content floor; bootstrap uses the separate one-anchor path; aliases count only as mapped anchors and kind/tag/scope/project metadata cannot satisfy topical content coverage |
| 535 — Retrieval V3 benchmark gate | `docs/evidence/RETRIEVAL_PRECISION_M3_BASELINE.md`, `docs/evidence/RETRIEVAL_M3_CURRENT_CANDIDATE.md`, content-free evaluator tests | Provisional local evaluation only: current synthetic evaluator is 10/10 on production and content-free; historical five-case and 17-case scorecards pass. The reported aggregate is not committed evidence, a live acceptance receipt, or a release gate |
| 536 — set-level marginal context selection | `set_selection.py`, `ContextCompiler`, `tests/unit/test_retrieval_high_cardinality.py`, `tests/unit/test_retrieval_bootstrap_composition.py` | Implemented locally: bootstrap relevant records must provide a complete topical set union; incomplete relevant tiers abstain rather than return partial context, while exact budget/omission accounting remains bounded and deterministic |
| 537 — synthetic retrieval usefulness evaluation | `bench/retrieval_usefulness.py`, sanitized fixture, isolated public-API vault, `tests/unit/test_retrieval_usefulness.py` | Developer-facing local evaluation only: the 17-case scorecard remains passing across current-fact, lifecycle, sensitivity, provenance, budget, and provider-shape gates; the harness refuses live Core data and grants no provider/client/release credit |

### 2026-08-26 Milestone 4 ambient project activation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| M4-01 — healthy project use requires no ATC UI | background `RuntimeCommand.core()` startup contract; MCP bootstrap instructions; `tests/unit/test_user_startup.py`, `tests/unit/test_mcp_contract.py`, `tests/integration/test_mcp_stdio.py` | Implemented locally: routine startup remains Core-only, MCP tells the host not to ask the user to open/manage ATC, and the real STDIO journey activates and returns one project capsule without a dashboard request. Setup, inspection, correction, and recovery UI remains optional |
| M4-02 — automatic activation respects authority and ambiguity | principal-filtered `build_project_runtime`; `activate_project_context`; authenticated `/v1/context/bootstrap`; `tests/unit/test_project_runtime.py`, `tests/unit/test_mcp_contract.py` | Implemented locally: explicit opaque/label/scope signals win; a compatible MCP roots backchannel may then contribute exactly one safe display name as a weaker hint, never a URI or path; one unique task label and then the sole authorized content-bearing project provide bounded fallbacks. Unauthorized anchors are removed before resolution and every invalid, missing, or ambiguous case abstains without project content |
| M4-03 — project context and retrieval remain bounded and available | Core bootstrap budget split, content-free activation audit, bounded projection-error fallback; focused project-runtime and MCP tests | Implemented locally: project context receives at most half the caller character budget, retrieval receives the remainder, `total_used_chars` stays within the request without changing retrieval-only `used_chars`, and project projection failure cannot take ordinary authorized retrieval offline. No default scanning, capture enablement, provider lifecycle, remote Edge, package, or release claim is made |

### 2026-08-25 bounded PR #88 lab reconciliation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Current-production benchmark snapshots reflect accepted strict retrieval semantics | `tests/unit/test_lexical_v3_benchmark.py`; `tests/unit/test_memory_lab.py`; `tests/unit/test_memory_lab_b01.py`; regenerated `bench/reports/memory_lab_baseline_ladder_wave2.*` and `bench/reports/memory_lab_b01_wave3.*` | Reconciled locally: lexical current Recall@5 `0.611111` and MRR `0.666667` remain below the unchanged V1 gates; M0 ATC is `0.6` success/recall with zero forbidden output; B01 ATC confirmatory CAOS is `0.285714`, while its fixture/config, boundary, accounting, and kill decision remain unchanged |
| Historical reliability reports are not current-production acceptance baselines | `tests/unit/test_memory_reliability_lab_e01b.py`; `tests/unit/test_memory_reliability_lab_e02_wave4.py`; immutable `bench/reports/memory_reliability_e01b_wave3.json` and `bench/reports/memory_reliability_e02_wave4.json` | Reconciled locally: exact assertions validate each report's recorded frozen base and preserve E01b's six unsupported/not-exercised semantics and E02's five `UNSUPPORTED` plus one `NOT_EXERCISED`; current disposable runs remain content-free boundary checks |
| Historical M3 retrieval precision baseline remains immutable | `bench/baselines/retrieval_precision_m3_f5e3a2b.json`; `docs/evidence/RETRIEVAL_PRECISION_M3_BASELINE.md` | Preserved byte-for-byte; no current-production lab result is written into or compared as a replacement for the historical snapshot |

Evidence is limited to the focused local benchmark/lab tests and generated
content-free reports described above. Full pytest, network, private data,
production retrieval changes, push/merge/release/publish actions remain out of
scope.

Evidence is aggregate local evaluation only, over sanitized synthetic or disposable local state. Repository documentation may record aggregate scores/counts, pass/fail results, test node IDs, fixture revisions, and evidence-boundary facts. Do not commit raw exports, workspace files, personal context, credentials, database files, per-record traces, generated reports, or other evaluation artifacts. No local result implies live provider, client, platform, release, or private-data acceptance.

### 2026-08-25 Milestone 5 lane D — ZF-017 through ZF-019 shadow foundation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| ZF-017 — observable outcome receipts | `packages/allthecontext/src/allthecontext/memory_lab_outcome_shadow.py`; `tests/unit/test_memory_lab_outcome_shadow.py` | Implemented as a bounded pure in-memory contract: assignment, exact project/projection versions, acknowledgement, declared use/nonuse, bounded action/tool envelopes, completion, typed external result/user correction, invalidation dependencies, idempotency, correction invalidation, terminal purge closure, secret refusal, ASCII machine-token validation, and duplicate-identity rejection are covered by 35 focused tests. No storage, Core route, MCP, dashboard, capture, scheduler, or retrieval wiring is present |
| ZF-018 — background consolidation in shadow | `propose_procedure` in `memory_lab_outcome_shadow.py`; focused shadow tests | Implemented only as deterministic advisory consolidation over sanitized typed receipt facts. Matching action signatures, recurrence across distinct task IDs, duplicate/conflict rejection, strong external verification, and lifecycle filtering are evaluated without a model, network, live data, provider, or production behavior |
| ZF-019 — procedural-memory gates | `ApplicabilityBoundary`, `RepairTest`, `PurgeClosure`, `ProcedureProposal`, `LearningDecision`; focused shadow tests | Implemented as fail-closed proposal gates requiring recurrence across distinct receipt/task identities or strong external verification, exact project/task/applicability-key matching, negative guards, passing repair tests, influence dependencies (project, projection, memory, and source) plus outcome dependencies, and closed purge coverage. Every result remains `advisory_only`; no learned authority, automatic truth write, or promotion path exists |

This lane is local research evidence only. Full-suite, hosted, release,
packaging, client/provider, private/live-data, network, and macOS acceptance
remain outside the evidence boundary.

### 2026-08-22 ZF-004 Wave 1 event reconciliation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Exact capture and lifecycle envelopes normalize independently into one bounded reference-only input | `experimental_event_reconciliation.py`; `tests/unit/test_experimental_event_reconciliation.py` | Implemented locally: capture operation and existing IDs/source generation/order/cursor/idempotency, lifecycle hook/session and exact payload/ownership/version validation, capture and payload-reference commitment/size, lifecycle `(client_id, event_id, sequence)` idempotency, context references, timestamps, retention/expiry, sensitivity, allow/deny authorization, and typed dependency withdrawals are retained without raw content; unlinked capture+lifecycle composition is rejected |
| Secret-like metadata and malformed evidence fail before a normalized input | `ReconciliationViolation`; isolated secret, exact-type, operation/generation, normalizer, payload-pairing, cursor-bound, and content-free error tests | Implemented locally with code-only failures; `as_dict()` exposes metadata and references only |
| Correction, delete, expiry, and purge dependencies withdraw safely | `DependencyWithdrawal`; delete-match and purge-action tests | Implemented locally: ordinary delete requires an authorized matching provider-item withdrawal, and terminal purge requires explicit `ERASE` |
| The slice does not create a second authority or persistence path | AST structural test; no direct storage/SQLite/network/provider SDK imports; no mutation, persistence, replay, cursor-advance, or observation/current-ID APIs | Implemented locally; Wave 2 Core/harness integration and provider capability claims remain out of scope |

Evidence is limited to deterministic synthetic unit tests and static structural
checks. Full repository pytest/mypy, hosted CI, provider access, private data,
release acceptance, and stable SDK/MCP lifecycle claims are not implied.

### 2026-08-22 ZF-006 Wave 2 Packet D zero-dashboard harness

| Requirement | Implementation/evidence | Status |
|---|---|---|
| One disposable journey composes capture, lifecycle, reconciliation, formation, Core policy, and authorized Retrieval V3 | `experimental_zero_dashboard_harness.py`; `tests/unit/test_zero_dashboard_harness.py`; sanitized `tests/fixtures/zero_dashboard_wave2.json` | Implemented locally as synthetic evidence: the existing deterministic fake adapter/ledger/coordinator and idempotent sink form five source observations through Core, an L2 fake host supplies pre-generation/direct-user/restart hooks, and Retrieval V3 compiles only authorized Core records. The projection closure check is separate content-free component evidence, not M3/Core/Retrieval integration. |
| First useful context and correction propagation are automatic | `run_zero_dashboard_journey`; `ZeroDashboardScorecard` | Implemented locally: phase-aware raw pack checks reject wrong-project, secret-like, inert-import, stale, expired, deleted, and purged facts in their applicable post-transition packs; direct evidence and a direct correction reach Core, and the next eligible compile contains the corrected value without the displaced value. Supersedes-output, query-adversarial wrong-project, unsupported-hook, and durable secret-absence checks close independently. The no-action claim is limited to the scripted fake-host trace, not operator telemetry. |
| Restart, cursor recovery, replay, authorization, retention, expiry, delete, purge, and zero future influence fail closed | retry/replay journey and scorecard gates; existing Core deletion/purge and Retrieval V3 selector/temporal boundaries | Implemented locally on a temporary SQLite database: a nonterminal checkpoint is resumed after Core close/reopen by a fresh coordinator/sink/adapter whose first call uses `cursor-1`; a later fresh completed replay directly compares equal Core counts with zero new capture events/observations/current records; the narrowed corrected record is absent for another principal; expiry is active at formation and absent from the later pack; ordinary delete and terminal purge have durable before/after proofs; post-restart time-to-first context is separately bounded. |
| Secret-like and imported material remain safe | direct-user reference resolver, formation refusal, Core opaque refusal receipt; inert imported fixture candidate | Implemented locally: the synthetic secret is held only in the test resolver, commitment-checked against the exact lifecycle `turn_ref`, refused before candidate persistence, absent from lifecycle envelope text and Core context state, and imported fixture text is retained only as tentative evidence with no current record. |

This is Wave 2 synthetic developer evidence only. It does not claim a real provider,
client/product acceptance, Memory Lab M3 integration, network/OAuth/client SDK,
scheduler, dashboard production behavior,
operator-vault access, private/live data, stable export, package/release
readiness, hosted CI, or full repository pytest/mypy acceptance.

### 2026-08-22 ZF-007 Wave 3 Packet E scheduler and health component

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Schedule capture around the existing Core coordinator with bounded retry and resource policy | `capture_scheduler.py`; existing `capture.py` capability/retry contracts; `tests/unit/test_capture_scheduler.py`; `tests/unit/test_capture_capabilities.py` | Component complete locally: disabled by default, reuses coordinator leases/checkpoints/cursors/event idempotency, honors bounded `Retry-After` or existing backoff, and applies per-connector concurrency/resource limits |
| Rotate bounded source selection and report truthful health | `CaptureScheduler._sources`; `CaptureScheduler._health_from_sources`; focused scheduler handoff tests | Component complete locally: source-page selection rotates in process, truncated health is explicitly `degraded`, and reauthorization actions are deduplicated in process; no durable scheduler or notification state |

### 2026-08-22 ZF-008 Wave 3 Packet F local Git/workspace component

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Read an explicitly authorized local root through the existing capture contract | `experimental_local_git_workspace_connector.py`; `tests/fixtures/local_git_workspace.py`; `tests/unit/test_local_git_workspace_connector.py` | Component complete locally: `fetch_page` fails closed before scanning unless the provider and `source.account_fingerprint` match `adapter.source_identity`; non-overlapping explicit roots only; deterministic snapshot/incremental events and Core coordinator replay reuse; partial coverage and network denial are declared |
| Fail closed at the local safety boundary | bounded scan/cursor constants and `CaptureScanReport`; provider/source-binding, AWS-shaped-secret, missing-root, secret-like, symlink/reparse, deletion, and over-20-file tests | Component complete locally: Git/dependency/credential paths and symlink/reparse paths are excluded, AWS `AKIA`/`ASIA`-shaped content is omitted, workspace text is inert, incomplete scans produce no partial page, and metadata cursors/samples/excerpts track at most 20 files |

### 2026-08-22 ZF-009 Wave 3 Packet G controlled reference host component

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Negotiate lifecycle capability truthfully and deliver context before generation | `experimental_reference_host.py`; `client_runtime.py`; `tests/unit/test_experimental_reference_host.py`; `tests/unit/test_client_runtime.py` | Component complete locally: in-process reference host accepts at most L2, ordinary MCP remains L0, L3 downgrades to L2, and pre-generation calls injected Core Retrieval V3 before delivery/generation; empty Core context fails closed before delivery or generation |
| Capture direct-user evidence and typed lifecycle checkpoints without overstating persistence | controlled-host fixture `tests/fixtures/reference_host_wave3.json`; typed checkpoint restore, ordering, retry-idempotency, integrity, L0, forged-session, and secret-refusal tests | Component complete locally: direct-user references are distinct from model self-attestation; typed snapshots restore events, trace, pending/delivered context, started-generation IDs, and sequencing state; the digest validates integrity only, the sink receives a stable retry idempotency key, L0/ordinary MCP resumes started IDs without fabricated context, L1+ retains request/delivery ordering, and no client-principal binding or production persistence is added |

### 2026-08-22 Packet H disposable proof stopped at source-fact admission

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Bind continuous Packet F evidence to deterministic source-fact promotion through Core | Stopped/removed Packet H disposable proof at the 2026-08-22 head; ADR-133 / PR #78 `registered_source_admission.py`; archive importer lifecycle remains separate | Historical 2026-08-22: the proof was open at the contract boundary and was stopped. PR #78 later merged the local registered-source admission seam. Packet H-D remains a foreground disposable proof. Later manual-cycle Packet E x Packet F evidence did not close the worker gap; the 2026-08-26 clean-vault worker journey closes that local developer gap only. Complete ZF-007/ZF-008 product exit and packaged/live support remain open |
| Advance to the next narrow frontier without overstating acceptance | ADR-132 (historical stop), ADR-133 / PR #78, ADR-137, ADR-139, ADR-141, ADR-142, ADR-143, ADR-144; focused successor tests/docs | PR #78 closes the local admission contract. Packet E and Packet G remain component-complete. PRs #82 and #84 later added CoreService/startup capture-runtime wiring and the opt-in Packet E scheduler. Packet H-D is disposable foreground proof, not complete Packet H/Phase 2 acceptance. Packet E x Packet F scheduled composition evidence is a later focused local proof, not ZF-007/ZF-008 product exit or complete Packet E/H or Phase 2. PR #86 merged compilation of those admitted records through Packet G and is not ZF-009 product exit. A later stacked local slice forms one caller-declared interaction preference in that same vault and is not ZF-010 product exit. ZF-007/ZF-008/ZF-009/ZF-010 product acceptance, the first real source/client journey, release, and support remain open; macOS remains absent/deferred |

The original component handoff counts were E: 25 tests, F: 25 tests, and G: 27
tests. Corrected focused counts are F/capture-capability: 27 tests and
G/client-runtime: 32 tests. The integrated F/G-adjacent union at corrected head
`719bdd9030e32ac34eb12184c35e1e47cf99cc37` passed 59 tests; Ruff,
format-check, and `git diff --check` passed. The previous pushed head
`dcf5de50b633ff00638c1396ddfcfb8ba04070e6` was fully hosted-green, but the
corrected head has not yet run hosted CI; full repository pytest/mypy also
remain open. These historical Wave 3/H-stop rows do not close
ZF-007/ZF-008/ZF-009 product acceptance, the first real continuous
source/client pair, ZF-010, complete Packet H, the Phase 2 acceptance
journey, release, or support status. Later PRs #82 and #84 added
CoreService/startup capture-runtime wiring and the opt-in Packet E
scheduler; that later wiring is not denied here. macOS remains
unsupported/absent/deferred under the current project truth.

### 2026-08-24 Packet H-D disposable integration reconstruction

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Reconcile bounded Packet F source admission through the PR #78 contract | `bench/packet_h.py`; `tests/unit/test_packet_h.py`; module and direct-file H-A CLIs | Implemented as disposable local evidence on merged PR #78: fake temporary ownership, redirecting `Path` subclasses, mutated/mismatched/nonempty/symlink/reparse roots fail closed and only capability-owned canonical roots are returned; the lstat/reparse branch is covered by a focused synthetic-stat test without privileged symlink creation; partial coverage and partial availability, denied network, and empty egress are gated; four fact-bearing upserts plus one deterministic no-fact upsert yield four structural records; candidate/record evidence must be the exact code-owned registered-source string; overflow fails closed with zero candidate/record output; crash/restart/replay is idempotent; CoreStores close in `finally` before teardown |
| Reconcile admitted records with public Memory Truth | `bench/packet_h_truth.py`; `tests/unit/test_packet_h_truth.py`; exact CLI `python -m bench.packet_h_truth` | Implemented as disposable local evidence: local-only posture and all-applied evidence are gated; complete public state plus stable identities are compared across list/detail/replay without private capture-lineage helpers; four current registered-source structural records have the expected provenance and metadata; exact withdrawal is bound to the public source-reference identity of the deleted provider item, preserves the non-withdrawal state, leaves three current and one deleted record without a new observation or ordinary tombstone, and requires deleted listing/status; public string fields are scanned in native, resolved, POSIX, and JSON-escaped path forms |
| Reconcile public Retrieval V3 search, bootstrap, and get behavior | `bench/packet_h_retrieval.py`; `tests/unit/test_packet_h_retrieval.py`; exact CLI `python -m bench.packet_h_retrieval` | Implemented as disposable local evidence: H-C calls the shared authoritative H-A validator before retrieval; required acceptance semantics are recomputed, the identifier digest is verified, malformed object trees are cleanly rejected, and additional true boolean predicates are allowed only when digest-bound and cannot bypass required predicates; returned search/bootstrap items used for acceptance must be structural; adapter deletion refuses absolute or `..` relative paths before unlink; 4/4 structural recall, provenance packaging, exact-get consistency, 256-character bootstrap compliance, three negative-query exclusions, real adapter deletion exclusion, and deterministic repeats pass |
| Preserve the evidence boundary | ADR-137; the four Packet H CLIs and focused Packet H tests | Local proof/lab evidence only over Packet F + PR #78 admission + public Memory Truth + Retrieval V3. Packet H-D is merged to protected main by PR #79; it is not released and does not itself satisfy continuous/scheduled Packet F acceptance. Later Packet E x Packet F scheduled composition evidence exists separately and still does not close ZF-007/ZF-008 product exit or complete Packet E/H acceptance. This H-D lane itself claims no Packet G reference host, ZF-010 automatic formation, full Wave 4 E–G composition, Phase 2 journey, provider/client support, archive import, OAuth or network support, ranking/schema changes, release readiness, or support status; no baseline receipt is added; macOS remains unsupported/deferred. PRs #82 and #84 separately wired CoreService/startup capture-runtime composition and the opt-in Packet E scheduler |

Reconstruction base is protected main after PR #78
(`e735d0dde301c64500acd1d404a2bbb6aab6724a`). The three Packet H test files
passed 61 tests in 32.15 seconds. Four CLIs passed with `PYTHONPATH` removed
so the checkout-source `sys.path` guard is active: `python -m bench.packet_h`
(0.86s), `python bench/packet_h.py` (0.91s), `python -m bench.packet_h_truth`
(1.01s), and `python -m bench.packet_h_retrieval` (1.62s).
Ruff check and Ruff format `--check` passed for the six Packet H Python files.
`python -m mypy packages/allthecontext/src` passed (91 source files).
`git diff --check` and `scripts/check_docs.py` passed. Full repository pytest
was intentionally not run in that reconstruction. The exact PR #79 pre-merge
head `34a0f96` had all 12 hosted required checks green, including 1,693 tests
on Windows and 1,693 on Ubuntu, CodeQL, security, dashboards, and desktop
artifacts.

Each public H-A/H-B/H-C run path obtains its root and capability only from the
shared fresh runner-owned temporary-root context. Its lexical construction
authority is not exposed by the modules; this is a construction/ownership rule,
not a hostile in-process security boundary. The context removes its temporary
state on exit. The CLI modules insert this checkout's repository root and
`packages/allthecontext/src` at the front of `sys.path` and fail closed if
imported `allthecontext` does not resolve under that checkout source.

### 2026-08-24 Packet G + Core Retrieval V3 lifecycle visibility

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Accepted L1+ pre-generation compilation fails closed without a Core principal | `experimental_reference_host.py::MissingCorePrincipal`; `compile_before_generation`; `tests/unit/test_experimental_reference_host.py` | Implemented locally: L1+ refuses before request/retrieval/delivery/generation when no `ClientPrincipal` is supplied. Ordinary MCP/L0 still returns `UnsupportedHookReport` and does not call the compiler |
| Next compile reflects Core lifecycle through the controlled host only | `experimental_reference_host_lifecycle.py`; `tests/unit/test_reference_host_retrieval_lifecycle.py` | Implemented locally as sanitized composition evidence: `ControlledReferenceHostV0` is the only compiler; authorized current decision and preference are visible; an ACL-private record is excluded for another principal; missing principal refuses before retrieval; correction includes the replacement and excludes the displaced value; ordinary delete, expiry, and terminal purge stay absent; one authorized record survives purge; restart/checkpoint restores host ordering/integrity only and does not duplicate Core truth; imported instruction-like direct text remains untrusted; secret-like input is refused content-free. Truth is seeded only through authenticated Core candidate/lifecycle APIs |

This slice does not close ZF-010, Packet E/F, complete Packet H, Phase 2,
CoreService/startup wiring, MCP lifecycle support, provider support, ranking or
schema changes, checkpoint persistence, hosted CI, full pytest, release, or
macOS. Empty-pack refusal remains covered separately by the existing Packet G
empty-context test.

### 2026-08-24 productized Packet E capture scheduler

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Keep scheduling explicit, Core-owned, and disabled by default | `CoreCaptureScheduler`; sidecar `capture-scheduler.json`; `ATC_CAPTURE_SCHEDULER_ENABLED=1`; `tests/unit/test_capture_scheduler_productization.py` | Implemented locally: missing sidecar and unset env do not dispatch; enable writes a content-free sidecar that survives restart when the process gate stays open; disable persists off and bounded-stops the thread while in-flight work completes; any presence of `ATC_UPDATE_HEALTH_OPERATION`, including empty string, force-disables even when the sidecar is enabled; lifespan uses the same helper; the scheduler sidecar inherits the Windows `O_NONBLOCK`/`O_NOFOLLOW` residual |
| Start one interruptible non-daemon thread after Core is ready and stop it on close | FastAPI lifespan; `CoreService.close`; bounded admin `stop`/`disable`; irrevocable `shutdown` fence | Implemented locally: start is after Core ready when gates pass, and admin enable can start or revive the worker only before shutdown; disable/stop join with a bound; lifespan `finally` and `CoreService.close` set a permanent closing fence and join the captured worker until dead before store/instance-lock release, without holding control/lifecycle mutexes; later enable/start cannot clear stop or revive/spawn in that instance; durable sidecar enablement may remain for the next Core process; the worker is never daemonized and is not cancelled; overlapping cycles are refused by an in-process global cycle lock, distinct from the coordinator's cross-process per-source lease; sidecar write plus start/stop are serialized by a control mutex; disable-then-enable during an in-flight cycle eventually runs while gates stay enabled and shutdown has not begun; `max_workers` is 1 |
| Recover expired runs and refresh the local-workspace adapter before due work | `recover_expired_runs` on Core start and each enabled `run_once`; `refresh_local_workspace_adapter` before each scheduled cycle | Implemented locally: expired reconciling sources become retry-due after recovery; adapter refresh matches admin `run`; due enabled/retry paths use existing coordinator/sink/adapter contracts; expected `CaptureError`/`OSError` stay content-free; programmer failures are not converted into a fake successful-empty report |
| Preserve `/health` and keep scheduler state authenticated and content-free | `GET /health`; `GET/POST /v1/admin/capture/scheduler`; `GET /v1/admin/capture/status`; `atc capture scheduler status`, `enable`, and `disable` | Implemented locally: `/health` remains exactly `{"status":"ok","component":"core"}`; scheduler status is admin-authenticated; CLI has status/enable/disable only and no `run_forever`/daemon; CLI sidecar status omits `running` because it cannot observe the Core process; status/health reads do not consume one-shot reauthorization or mutate rotation; invalid config and public payloads stay path/secret free |

This slice is not complete Packet E product acceptance, complete Packet H,
ZF-010, provider or network support, hosted/full-suite acceptance, release, or
macOS support. Local-workspace source lifecycle remains explicit.

### 2026-08-24 Packet E x Packet F scheduled composition evidence

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Drive authorized local-workspace ingestion through the merged opt-in scheduler | `CoreService.capture_scheduler.run_cycle`; existing process/env/sidecar gates; injected `CoreService` clock; `tests/unit/test_scheduled_packet_f_composition.py` | Implemented locally as composition evidence: an isolated CoreService vault authorizes and enables one Packet F source; two focused tests call `run_cycle()`, the same method used by the background loop, without starting that thread; a due cycle admits four structural current records observed through public Memory Truth and Retrieval V3 rather than SQL row counts. This is not Packet H; the proof reuses Packet H-D truth/retrieval helpers |
| Prove incremental withdrawal, update, and restart idempotence on public surfaces | public `list_memory_truth` / `get_memory_truth` / `memory_truth_coverage`; Retrieval V3 search, bootstrap, and get; reused Packet H truth/retrieval helpers | Implemented locally: a not-yet-due cycle creates no new public records; after the injected clock advances, deleting one item and changing another yields exact source-reference withdrawal, in-place update of the same current identity with changed public `binding_hash`, no duplicate current records, and no ordinary tombstone; restart plus a third unchanged due cycle applies zero events and leaves public truth/retrieval identical |
| Keep negative scheduler gates from creating public records | `ATC_CAPTURE_SCHEDULER_ENABLED`; sidecar `enabled: false`; `ATC_UPDATE_HEALTH_OPERATION` including empty string | Implemented locally: each gate leaves Memory Truth current items empty and coverage `record_count` at zero. Existing content-free scheduler status is a public non-mutating read (`running` remains false) and does not expose captured text, paths beyond existing policy, credentials, or raw personal context |

This is Packet E x Packet F scheduled composition evidence. It is not
ZF-007/ZF-008 product exit, complete Packet E product acceptance, complete
Packet H, Phase 2, real provider or client support, hosted/full-suite
acceptance, release, or macOS support. Continuous/scheduled Packet F
acceptance remains open. This evidence is on protected main through merged
PR #85 at `15d313f8bee33717e3e59f2583599df5305ca4fd`. PR #86 later merged
Packet G compilation over those admitted records; it does not close this
Packet F evidence boundary.

### 2026-08-24 Packet G compilation over scheduled Packet E x Packet F records

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Compile only public registered-source records admitted by the scheduled Packet E x Packet F journey | `experimental_reference_host_lifecycle.py::compile_authorized_pack`; `ControlledReferenceHostV0`; `tests/unit/test_scheduled_packet_g_composition.py`; extracted helpers in `tests/fixtures/scheduled_packet_f.py` | Implemented locally as composition evidence in one disposable `CoreService` vault: the scheduler-driven initial due cycle admits four public registered-source records; L2 in-process compile feeds only those current public identities into the existing Packet G compile surface; compiled items are structural, provenance packaged, scope-qualified to `workspace.structure`, and delivered as `context_pack` references whose SHA-256 matches pack content. The 256-character bootstrap compile truncates for budget; a separate 4000-character compile omits the duplicate Markdown structural sentence through Retrieval V3 duplicate suppression without treating that omission as truncation. Those are distinct existing bounded compile behaviors, not a 1:1 Memory Truth dump |
| Keep compilation authorization-first and capability-qualified | `MissingCorePrincipal`; L0 / ordinary MCP `UnsupportedHookReport`; empty-pack `ClientRuntimeContractError` | Implemented locally: missing `ClientPrincipal` refuses before retrieval; L0 and ordinary MCP do not invoke the Core compiler; a closed scheduler process gate leaves zero public records so L1+ empty Core context fails closed before delivery/generation |
| Keep compiled context content-safe and untrusted-path refusing | focused Packet G composition leak/untrusted/secret checks | Implemented locally: compiled host material does not include workspace roots, captured source text, credentials, or raw personal context; instruction-like direct-user text remains an untrusted envelope and is absent from the pack; secret-like input is refused content-free. After the scheduled incremental delete/update cycle, the withdrawn record ID is absent from the next compile |

This is Packet G compilation/composition evidence over merged Packet E x
Packet F scheduled capture. It is not ZF-009 product exit, ZF-010 product
exit, complete Packet H, Phase 2, provider or client support,
hosted/full-suite acceptance, release, private-data evidence, or macOS
support. No Packet G, scheduler, retrieval, or formation production
behavior was added. Continuous/scheduled Packet F acceptance remains open.
This evidence is on protected main through merged PR #86 at
`f06961e7aaefc37f6f7f3b86d16d50d983cedca7`. A later stacked local slice forms
one caller-declared interaction preference in the same vault and is not
ZF-010 product exit.

### 2026-08-24 same-vault ZF-010 composition over scheduled Packet G records

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Form one caller-declared interaction preference in the same vault as scheduled Packet E x Packet F records and Packet G compile | `experimental_reference_host_formation.py::form_direct_user_turn`; `tests/unit/test_scheduled_zf010_same_vault.py`; reused `tests/fixtures/scheduled_packet_f.py` | Implemented locally as stacked composition evidence in one disposable `CoreService` vault: Packet F setup helpers authorize and enable a sanitized workspace; one due Packet E cycle admits public registered-source records without starting the scheduler thread; Packet G compile of those public IDs is setup, not a repeat of Packet G budget/refusal assertions. A durable `context:read` principal compiles through `ControlledReferenceHostV0`, `compile_authorized_pack`, and `core_retrieval_compiler`. A separate durable witness principal with `witness:explicit_user_statement` forms through an L2 host whose `client_id` matches that principal |
| Prove APPLIED current public truth, in-place correction, and forget without mutating scheduler-admitted records | public `list_memory_truth` / `get_memory_truth`; next Packet G compile contents | Implemented locally: observe-only envelopes are not Core persistence and keep content out of `turn_ref` commitments; `form_direct_user_turn` with an aware frozen timestamp yields disposition `APPLIED`, current public Memory Truth, and `source_id is None`; mapper status `formed` is not treated as current by itself. The next compile includes the preference while scheduler-admitted public IDs remain valid. A correction keeps the same record identity, puts new content in the next compile, and leaves registered-source fingerprints unchanged. A `context_forget` makes the preference non-current/deleted through public truth, removes both preference texts from the next compile, and leaves registered-source records current. Exact Packet G selected counts are not reasserted after the preference because mandatory preferences change budgets |
| Keep distinctive same-vault refusals fail-closed without targeting registered-source facts | compile-reader formation, undeclared-kind, lookalike envelope, unformed instruction-like observe, secret-like observe | Implemented locally: formation through the compile reader cannot create current truth; unsupported caller-declared `project_decision` is `undeclared_kind`; a lookalike copied envelope is refused by object-identity membership; instruction-like content is observed and never formed, so it is absent from Core truth and the Packet G compile; secret-like input is refused content-free. Kind is never inferred. No `project_decision` or working-state formation is claimed |

This is stacked local composition evidence, not ZF-010 product exit, complete
Packet H, Phase 2, provider or client support, hosted/full-suite acceptance,
release, private-data evidence, or macOS support. No Packet G, scheduler,
retrieval, or formation production behavior was added. Continuous/scheduled
Packet F acceptance remains open. This candidate remains a local checkout
stacked on merged PR #86 at protected main
`f06961e7aaefc37f6f7f3b86d16d50d983cedca7` until pushed and merged.

### 2026-08-24 productized foreground local-workspace capture runtime

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Compose CoreService and CLI through one capture runtime | `capture_runtime.py`; `core/service.py`; `cli.py`; `tests/unit/test_capture_runtime.py` | Implemented locally: both surfaces construct `CaptureCoordinator` only through `compose_capture_coordinator`; the registered-source sink is always injected; the local-workspace adapter is registered only for a valid machine-local sidecar |
| Authorize exactly one canonical local workspace root without leaking the path | `authorize_local_workspace`; `atc capture authorize-workspace`; private sidecar under `CoreConfig.data_dir` | Implemented locally: explicit absolute `--root` plus local-only acknowledgement; FileLock held across read/identity/complete inventory/reconcile/write; newly created identity-bound `local-git-workspace` source is disabled with scopes exactly `workspace.structure`, account label `local-workspace`, and a non-revoked lifecycle; reconciliation preserves the existing acceptable enabled/paused/degraded/reconciling lifecycle rather than resetting it to disabled; root stays out of account labels, public status, logs, receipts, portable export, and fixtures; changed roots are a new identity and are refused; simultaneous same-root authorize yields one source and different-root authorize yields one winner plus bounded refusal; fail-closed authorization errors suppress OSError context |
| Keep Core available when authorization is absent or invalid | CoreService composition fail-closed adapter registration | Implemented locally: missing/invalid/symlink/reparse/non-regular/oversize sidecar; descriptor-based sidecar read (`os.open` with available close-on-exec/no-inherit/nonblocking/nofollow flags, `fstat`, 1..16 KiB regular file, MAX+1 bounded complete read, post-open `lstat`/`os.path.samestat`/reparse refusal); missing/non-directory/symlink/reparse/parent-redirecting/UNC/extended-UNC/Windows-remote/implicit home/cwd roots; post-resolve `os.path.samestat` rather than `Path.samefile`; held authorization lock; incomplete inventory; unreadable/malformed capture rows including object/string `requested_scopes_json` shapes; and retargeted sidecar identity leave the vault available and capture skipped as `capture_adapter_unavailable`; authorize returns a bounded content-free `CaptureError` rather than decoder exceptions or raw row/path. Explicit Windows extended local-drive prefixes unwrap to the ordinary drive form. Windows named-pipe sidecar hang without `O_NONBLOCK` remains a residual |
| Inventory and validate every workspace source before register/reconcile | bounded `list_sources` pagination in `capture_runtime`; focused >100-source, 500-row page-boundary, and metadata tests | Implemented locally: inventory is not truncated at 100 rows and crosses the 500-row page boundary; unreadable or malformed rows fail closed without crashing Core; adapter registration and authorize reconciliation require exactly one canonical matching row and refuse malformed, duplicate, mismatched, and revoked rows without deleting ledger state |
| Keep generic create from racing the reserved workspace provider | CLI `atc capture create`; admin `POST /v1/admin/capture/sources`; `reject_reserved_workspace_provider` | Implemented locally: public generic create rejects `local-git-workspace` after the same Unicode `str.strip` normalization as `CaptureLedger`, including leading/trailing/tab whitespace, with `capture_authorize_workspace_required`, and preserves other providers; `CaptureCoordinator.create_source` remains the provider-neutral test seam |
| Produce Memory Truth / Retrieval V3 records from one foreground run | existing capture coordinator, registered-source sink, and retrieval engine through the shared runtime | Implemented locally for manual opt-in foreground capture after enable: structural facts and a deterministic no-fact, restart identity rebuild with idempotent replay, exact file-deletion withdrawal, and Core-authoritative correction/delete/purge barriers. Admin run refreshes the local-workspace adapter fail-closed immediately before execution so a sidecar authorized after Core startup can run without restart and a later invalid sidecar is unavailable. CLI run still composes a fresh coordinator. The later Packet E scheduler slice is a separate explicit Core opt-in |

The runtime slice is not complete Packet H, ZF-010, provider or network
support, hosted/full-suite acceptance, release, or macOS support. Packet E
scheduling is a later isolated Core opt-in documented above.
Revoked and pre-existing malformed workspace-source rows have no product
recovery here; durable database uniqueness remains later hardening.

### 2026-08-24 ZF-010 direct-user formation mapper (local composition evidence)

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Map only accepted in-process Packet G L1+ `direct_user_turn` envelopes into Core through existing contracts | `experimental_reference_host_formation.py`; `tests/unit/test_reference_host_formation.py`; existing Packet G compile helper remains compile-only | Implemented locally as sanitized composition evidence: L0, ordinary MCP, unsupported-hook reports, other hooks, and lookalike envelopes are refused; membership is in-memory object identity in `host.events`, including after typed checkpoint restore of those same objects; restore is not Core persistence; caller-supplied content is commitment-checked as exact UTF-8 length plus SHA-256 against `turn_ref`; envelopes never store content; durable Core `ClientPrincipal` is required with `envelope.client_id == principal.id`; Core rebinds registered scopes; `normalize_lifecycle_event` then `form_observation`; closed caller-declared kinds are `interaction_preference` with `supersedes=None`, `correction` with required nonblank `supersedes`, and `context_forget` with required nonblank `supersedes`; preference rejects any non-None supersedes before `add_candidate` and cannot mutate preference or `project_decision` targets; kind is never inferred; `CandidateInput.source_id` stays `None`; entity/attribute slots are rejected; `add_candidate(..., client=principal)` only |
| Keep formation fail-closed for authorization, secrets, retention, observation time, and replay | focused formation tests plus existing G/client/lifecycle tests | Implemented locally: missing/wrong principal and forged scopes fail closed; a different witness correction/forget against owner-private truth is `IGNORED` and does not mutate it; scopes that are `str`/`bytes` or invalid items are refused; allowed/denied overlap is `DirectUserFormationError`; commitment mismatch, missing targets, any retention class except `bounded`, and over-bound content are refused without truncation; missing/naive observation time is refused and `datetime.now` is not synthesized; when the envelope lacks a valid timestamp the caller supplies an aware observation time that is stamped deterministically; secret-like content is absent from envelopes, candidates, records, and refusals; secret-refusal retry in-process and after CoreStore reopen returns the same receipt id with `replayed=true` using a UUIDv4-shaped operation id derived from `client_id+event_id+sequence`; instruction-like imported text is not auto-formed; public versus caller-requested private ACL; idempotent retry/restart/checkpoint-restore does not duplicate; AST/import boundaries forbid `delete_record` / `purge` / `correct_record` / `IngestionService` / `LOCAL_ADMIN` / event-log scanning / `datetime.now` |

This mapper is local composition evidence, not ZF-010 product exit, Packet E/F/H,
Phase 2, CoreService/startup wiring, MCP lifecycle support, provider support,
ranking or schema changes, checkpoint persistence, hosted CI, full pytest,
release, or macOS. Packet G checkpoint restore is in-memory identity membership
only. ADR-139 is the foreground capture runtime decision.

### 2026-08-23 registered-source admission PR1 contract

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Admit only a Core-issued registered-source structural fact through the existing capture sink | `registered_source_admission.py`; migration `016_registered_source_admission.sql`; `memory_policy.py`; sanitized `tests/unit/test_registered_source_admission.py` | Implemented locally for the bounded PR1 contract: exact durable event/run/source validation, exact code-owned `workspace.structure` scope, closed local-workspace extractor registry, complete code-owned projection validation, opaque source/item memory references, Core availability, normal sensitivity, empty ACLs, explicit false, deterministic capture-lineage record IDs, replay idempotency, source withdrawal, deterministic no-fact upsert withdrawal for the same exact source/item, correction/delete/purge/no-linkage barriers, and content-free receipts. No CoreService, package-startup, scheduler, or reference-host wiring |
| Keep machine-local capture runtime out of portable archives without losing admitted Core truth | `export.py`; portable export/restore focused test | Implemented locally: all five capture runtime tables are omitted even for source-inclusive exports, registered candidate capture FKs are nulled, legacy capture table entries are ignored on restore, and same-database restart retains capture state |
| Advance ADR-132 without overstating acceptance | ADR-133; focused local tests only | PR1 / PR #78 closes only this local admission contract. Complete Packet H, ZF-010, product/provider support, hosted/full-suite acceptance, stable SDK, production wiring, release readiness/publication, and macOS remain open, absent, or deferred |

### 2026-08-23 bounded capture page-recovery correctness

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Persist and repair one bounded pending page without adding a truth table | `017_capture_page_recovery.sql`; `ensure_capture_schema`; focused capture migration/restart tests | Implemented locally: migration 017 owns only nullable pending checkpoint fields and a bounded JSON array; marker-present and missing-column repair remain restart-safe; migration 016 remains limited to its three candidate columns and partial index |
| Stage a complete page atomically before sink admission and recover it before provider fetch | `CaptureLedger.stage_page`; `CaptureCoordinator._recover_pending_page`; focused capture crash/rollback/retry tests | Implemented locally: existing event identity/idempotency/conflict rules are reused, ordered durable event IDs are persisted in the same transaction, applied pending events replay idempotently, all events must apply before cursor advance, and repeated sink failure remains bounded/retryable |
| Recover registered-source admission and real local deletion without generic absence deletion | existing registered-source sink; sanitized `tests/unit/test_registered_source_admission.py` recovery/delete test | Implemented locally: after sink admission/capture-commit interruption and fixture-file removal, same-run recovered-cursor diff emits the source-scoped delete, capture item is deleted, no ordinary tombstone is minted, and pending state clears; correction, availability, ordinary-delete, and purge barriers remain Core-authoritative |
| Keep scope truthful | ADR-134; focused local checks only | That capture-correctness work claimed no complete Packet H, production startup wiring, scheduler, provider/product support, private data, macOS support, or full-suite acceptance |

### 2026-08-23 capture admission and repair guard reconciliation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Reject duplicate provider event IDs before page staging and guard durable pending IDs for uniqueness | `CaptureCoordinator` page validation; `CaptureLedger.stage_page`; duplicate-page focused lane | Implemented locally: duplicate IDs within a page fail before staging, the pending durable ID list is uniqueness-guarded transactionally, and partial pending state cannot survive the rejection. Historical lane evidence: 58 focused tests |
| Recover legacy duplicate pending IDs without weakening new-page rejection | `CaptureLedger._pending_event_ids`; focused poisoned-marker recovery regression | Implemented locally: raw bounded marker lists and each ID remain validated, repeated identical durable IDs are replayed once in first-occurrence order, and successful recovery atomically advances/clears the marker; malformed marker data still fails closed |
| Keep local workspace `workspace.structure` events metadata-only | `LocalGitWorkspaceCaptureProviderAdapter`; registered-source projection; metadata-focused lane | Implemented locally: the adapter-produced/coordinator path emits bounded structural metadata only; the generic ledger retains internal caller-supplied payloads and the registered sink keeps extra fields inert. Source text and excerpts are not durably retained on the adapter path or in registered-source candidate/evidence projection. Historical lane evidence: 63 focused tests |
| Bound capture schema repair to already-applied capture migrations before applying a newer migration | `ensure_capture_schema`; `CoreStore.migrate`; capture migration-focused lane | Implemented locally: repair runs through the already-applied capture version inside the pending migration transaction, and successful repair retains the complete repaired state. Historical lane evidence: 8 capture migration tests. `docs/architecture/DATA_MODEL.md` already records 017 as used and 018 as next |
| Keep validation evidence bounded and accurately scoped | Historical lane reports; integration owner's subsequent combined focused run | The integration owner subsequently ran 152 combined focused tests on integrated code. Reported Ruff lint, Ruff format-check, and mypy checks passed; these local reports do not constitute full-suite acceptance |
| Preserve the project boundary | ADR-135; focused/static reports only | That capture-correctness work claimed no complete Packet H, production startup wiring, scheduler, provider/product support, private-data evidence, macOS support, or integrated full-suite acceptance |

### 2026-08-22 draft-PR formatting and CI-trigger reconciliation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Changed Python source satisfies the formatter gate used by hosted CI | `python -m ruff format .`; subsequent local `python -m ruff format --check .` | Implemented locally for the 23 files reported by the initial PR 73 Windows and Ubuntu jobs |
| Feature-branch validation runs one canonical CI matrix | `.github/workflows/ci.yml` limits `push` to `main` and `v*` tags and retains `pull_request`; release workflows remain manual | Implemented locally: pull requests retain the full supported Windows/Ubuntu, dashboard, security, parity, and desktop matrix; merged `main` and version tags retain push evidence; ordinary feature branches do not run push CI; no job or gate is weakened |

Hosted revalidation of this exact follow-up commit remains pending. The trigger
change does not alter the unsupported-macOS posture or the manual release,
candidate, and beta-channel ceremonies.

### 2026-08-22 complete-source coverage repair

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Sources dashboard retry repairs a source that is terminal-complete but coverage-incomplete | `ArchiveImportService.reprocess_source`; existing `publish_source_rebuild` authority; `test_complete_source_with_incomplete_coverage_repairs_from_preserved_blob`; dashboard retry contract regression | Implemented locally: non-rebuild reprocess routes only `import_status=complete` plus explicit `coverage_complete=false` into the preserved-blob rebuild path, which publishes only after complete coverage |
| Repair failure and concurrent retry remain safe | `test_incomplete_coverage_repair_failure_keeps_prior_records`; `test_concurrent_incomplete_coverage_repairs_are_idempotent`; `test_complete_healthy_source_reprocess_remains_a_noop` | Implemented locally: parser failure does not withdraw prior current records, concurrent callers converge on one rebuild generation, and healthy complete sources are not reparsed |

Evidence uses synthetic payloads and temporary local databases only. Dashboard
check, full test suite (55 tests), and production build pass locally. Full
pytest, hosted CI, release/publication, live/private data, and macOS execution
are not claimed here.

### 2026-08-22 generic bounded-failure coverage correction

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Standalone bounded CSV failures return truthful closed coverage without raising | `_parse_csv_document`; `_GenericCoverage`; `test_oversized_standalone_csv_is_unavailable_and_closed` | Implemented locally: an oversized synthetic CSV closes exactly one `unavailable` item, exposes the matching generic stat, returns no candidate, and remains incomplete |
| Every generic terminal failure reason maps to one declared counter | `_generic_failure_result`; `_combine`; `test_generic_failure_reasons_map_to_one_closed_counter` | Implemented locally for `unavailable`, `failed`, and `unparsed`; each result has exact seven-key accounting with a sum of one and no dynamic slotted-attribute assignment |

This correction is focused synthetic importer evidence only. Full pytest,
hosted checks, provider access, private data, release action, publication, and
macOS execution remain outside scope.

### 2026-08-22 hosted full-suite reconciliation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| A newly initialized vault reports the migration generation it actually applied | `CoreStore.initialize_vault`; `test_new_vault_schema_version_matches_applied_migrations`; complete recovery-admin file | Implemented locally: the insert binds `vaults.schema_version` to the latest applied migration, so later export/integrity verification does not mutate logical vault identity merely by reopening it |
| Frozen B01 expectations describe the accepted deterministic retrieval implementation | `test_b01_twenty_repeat_result_is_deterministic_identifier_safe_and_bounded` | Reconciled locally: `atc-retrieval-v3` confirmatory CAOS is 3/7 after the accepted query/ranking changes; fixture/config hashes, twenty-repeat determinism, zero external/model use, and the final kill decision remain unchanged |

The combined focused reconciliation is 24/24 green. The hosted full matrix for
the exact follow-up commit remains pending; no release or platform acceptance is
claimed from the local rerun.

### 2026-08-22 offline product-correctness maintenance

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Unicode-equivalent ZIP paths resolve to one logical member | NFKC/casefold identity in `parse_zip_bundle`; `test_unicode_equivalent_zip_member_collisions_are_deterministic` | Implemented locally: archive order selects the first member and later compatibility-equivalent names close as duplicates with bounded diagnostics |
| Capture integers fit durable storage and begun runs terminate on local failure | `MAX_CAPTURE_INTEGER`; `CaptureCoordinator.run`; focused capture range/recovery regressions | Implemented locally: generation, page order, and payload integers are signed-64-bit bounded before storage, and an unexpected local exception produces a content-free failed run instead of a nonterminal job |
| Pagination integers are strict | `SearchRequest.limit`; `SearchRequest.offset`; Core API cursor regression | Implemented locally: bounded integers remain accepted and JSON booleans receive HTTP 422 instead of becoming `1` or `0` |
| Direct record and registered-client lookup remain vault-local | `CoreStore.get_record`; `get_memory_truth`; client authentication/observer/list/count/revoke queries; focused temporary-database regressions | Implemented locally: synthetic rows assigned to a second vault are absent from authoritative record reads and cannot authenticate or appear in registered-client administration |

Evidence is limited to synthetic in-memory archives and temporary local
databases. No network/provider access, private data, full pytest matrix, hosted
checks, release action, publication, or macOS work is claimed.

### 2026-08-22 adversarial boundary sweep

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Unicode-equivalent direct credentials fail before durable state | `secret_boundary.py` detector v4; Core/Relay refusal paths; `tests/security/test_preledger_secret_boundary.py` | Implemented locally for compatibility-width, zero-width, and combining-form projections while retaining high-confidence matching and opaque content-free receipts. Compact JWT/JWE, PASETO, selected provider-prefixed, and contextual bearer/token forms are covered structurally without entropy-only rejection; ordinary health, address, and SSN context remains admissible. No raw secret, payload digest, or private fixture is retained |
| Canonical supersession remains acyclic and vault-local | `CoreStore._validate_supersedes_tx`; correction, approval, restore, automatic-create, and rebuild-reapply write paths; `tests/unit/test_memory_truth.py` | Implemented locally: missing, overlong, cross-vault, self, and bounded-chain cycles fail transactionally before canonical mutation. Self/two-node regressions keep temporal retrieval operational |
| ZIP member identity cannot collapse after diagnostic truncation | `_validate_zip_member_name`; `tests/unit/test_provider_ingestion.py` | Implemented locally: names over 1,000 characters or containing non-printable characters close unavailable with bounded escaped warnings; distinct long names are not reported as duplicates or silently preferred. Raw-first preservation remains unchanged |
| Capture sink cannot redirect canonical lineage | `CaptureCoordinator._apply`; strict capture value normalization; `tests/unit/test_capture.py` | Implemented locally: Unicode-obfuscated credential markers and implicit ID/integer coercions fail closed, and a sink receipt must return the exact deterministic source/item lineage before any item/checkpoint commit |
| Retrieval diagnostics remain finite and bounded under malformed inputs | `retrieval_contracts.py`; `DeterministicUsefulnessReranker`; `tests/unit/test_retrieval_contracts.py`; `tests/unit/test_retrieval_usefulness.py` | Implemented locally for strict diagnostic/selection primitives and neutral handling of NaN, infinity, or nonnumeric lexical scores. Authorization, temporal, and admissibility boundaries remain ahead of relevance |

The sweep is focused local engineering evidence. Full pytest, hosted CI,
exact-artifact/client/provider acceptance, live/private data, publication,
release acceptance, and macOS execution remain unclaimed.

### 2026-08-22 Sources/Context dashboard reconciliation

The accepted dashboard now maps the integrated contracts without flattening
state:

- `apps/dashboard/src/types.ts` and `api.ts` define defensive import, exact
  closed-coverage, context coverage, and selected-truth shapes. Record and
  truth values are constructed field-by-field with bounded primitives/enums;
  malformed detail envelopes fail content-free, malformed list rows are
  omitted, and unknown or missing legacy metadata is visible as unavailable
  rather than fabricated accounting. Import IDs and displayed statistics
  accept only valid bounded nonnegative integers.
- `apps/dashboard/src/App.tsx` and `styles.css` preserve the accepted desktop /
  mobile layout and existing actions while separating source item accounting
  from terminal processing, rendering real Core coverage, and showing the
  selected truth status, conflict, provenance, evidence, and history.
- `apps/dashboard/src/api.test.ts` and `App.test.tsx` provide focused evidence
  for normalization, terminal/item split, coverage failure fallback and
  recovery, malformed-wire filtering, honest count handling, no-N+1 truth
  selection, stale-response protection, mutation refresh, and rendered
  statuses. A failed coverage refresh clears cached truth metrics while
  preserving the independent search result counts. The dashboard suite is 54
  tests across 2 files.

This is a local-only beta.6 review candidate. Fresh independent API/DOM review
accepted the exact hardening commit. Synthetic loopback browser checks do not
inspect real exports or live/private Core data and do not constitute release or
fresh visual Product Design acceptance; visual acceptance remains pending.

### 2026-08-22 Continuous Capture foundation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Provider-neutral capture contracts and bounded ledger | `capture.py`; migration `015_continuous_capture.sql`; `docs/protocols/CAPTURE.md`; `tests/unit/test_capture.py` | Implemented locally for source/checkpoint/event/item/run storage, canonical marker-present repair, exact schema constraints/indexes, lifecycle transitions, typed lease capability authority, ordered page replay, stable lineage, canonical errors, lag/backoff/lease telemetry, and content-free projections. No real provider, network, OAuth, scheduler, dashboard, or package-startup behavior is implemented or claimed |
| Exact stage/apply/commit replay semantics | `CaptureCoordinator`; `CaptureApplicationSink`; deterministic fake adapter/sink; crash-before-commit replay tests | Implemented locally: durable staging precedes the injected idempotent sink; receipt, item mapping, and event checkpoint commit atomically; duplicate replay is a no-op; failed/out-of-order/gap events do not advance the checkpoint. Full snapshot/rescan deletion is deferred |
| Authenticated content-free admin API and CLI | `/v1/admin/capture/*`; `atc capture ...`; API/CLI tests | Implemented locally with existing admin authentication and loopback defaults. Cursors, payloads, credential references, provider tokens, and raw errors are not exposed. If no adapter is registered, run fails safely with `capture_adapter_unavailable` and no network call |
| Current product/release availability | beta.6 status and identity docs retained; no dashboard/package startup edits | Independent security/correctness/API review accepted the local foundation only. Real provider availability, hosted CI, release acceptance, publication, live/private data review, and macOS acceptance remain outside scope |

### 2026-08-22 Continuous Capture adapter-availability ownership correction

| Requirement | Implementation/evidence | Status |
|---|---|---|
| A coordinator without an adapter cannot invalidate another coordinator's live leased run | `CaptureCoordinator._mark_unavailable`; `tests/unit/test_capture.py::test_missing_adapter_does_not_invalidate_live_run_on_shared_database` | Implemented locally: the adapter-missing probe atomically observes a future-expiring run before degrading; it preserves `reconciling`, retry/operator state, and content-free errors, and the owning coordinator can renew and finish. No provider, network, scheduler, or release behavior is claimed |

### 2026-08-22 provider-terminal Import Truth correction

Provider-shaped empty roots, zero-message conversations, and malformed provider
siblings now close exactly one logical terminal result. Known-provider empty
roots/conversations are `skipped`; identity-free provider-shaped empties and
malformed entries are `unparsed` and keep coverage incomplete. Provider
containers remain structural in raw ZIP accounting and are not double-counted.
The bounded parser carries explicit root versus root-array-item context beside
each streamed value, so an empty object or wrapper sibling is `unparsed` exactly
once while standalone known-provider empties and zero-message conversations
remain `skipped`. No full root is materialized and terminal context is not
filename-derived; malformed-entry coverage and completion remain invariant under
permutation across direct, path, and ZIP entrypoints.
Allowed neutral alternate JSON basenames establish ChatGPT attachment scanning
only after a valid bounded content signature and a successful complete iterator;
the signature buffer retains no JSON root. A valid provider-looking prefix with
trailing data or any later parse, depth, item, or byte-limit failure stays
generic, closes one `unparsed` logical item, and cannot enable provider-specific
attachment inventory or links. A malformed neutral sibling cannot poison a
separately valid named provider member. Focused tests cover all three terminal
cases, adversarial permutations, four alternate names, direct/path/ZIP parity,
bounded failure classes, and the negative neutral case. This is synthetic
engineering evidence only and grants no acceptance credit.

### 2026-08-22 bounded ordinary-JSON and exact-coverage correction

Direct bytes, filesystem paths, and ordinary ZIP JSON now share a strict
incremental bounded reader with explicit 512 MiB byte, 128 MiB item/document,
and 128-level quote/escape-aware nesting limits. Validate-then-consume keeps
trailing-data, malformed, depth, and recursion failures atomic with no partial
candidates. Empty ordinary JSON roots close as one skipped logical item across
direct/path/ZIP entrypoints, while provider containers stay structural and
semantic-item-driven. Provider raw classification uses the canonical and dated
conversation filenames plus the exact alternate-name/provider-context rules in
ADR-109; neutral malformed alternates remain ordinary rather than being
silently promoted. `CoverageReport` normalizes omitted/partial maps to the exact
seven-key zero-filled contract and rejects unknown or invalid counts. Focused
synthetic tests cover all five acceptance blockers; this change grants no
acceptance credit and does not inspect live/private data.

### 2026-08-22 final import terminal-partition correction

The import boundary now keeps malformed provider containers structural in the
raw ZIP audit while assigning their logical failure exactly once to the closed
seven-key map. Provider-memory/profile values rejected by bounded content
policy close as `skipped` logical items rather than leaving an all-zero
denominator. Standalone decoding is strict UTF-8, standalone CSV is supported
atomically through both public archive entrypoints, and ordinary JSON roots use
bounded two-pass validation/consumption without raw temporary artifacts.
Enumerated ZIP count, size, ratio, encryption, and path/depth rejections return
content-free member closure without reading rejected payloads; an unenumerable
ZIP returns a distinct archive-level failure with no fabricated member closure.
Focused synthetic regressions cover these equations and terminal contracts.
This change grants no acceptance credit and does not inspect live/private data.

### 2026-08-22 final import-truth contract correction

Declared JSON `.dat` members are validated atomically before candidate
publication, so malformed trailing bytes cannot leave partial candidates while
also incrementing `unparsed`. Packaged-provider acceptance uses the shared
`CoverageReport` validator for closed keys, strict bounded counts, and
completion consistency. Synthetic focused tests cover the malformed trailing
JSON regression, valid JSON/JSONL attachment paths, reconciler coercion,
unknown/bounded counts, and unavailable/duplicate/failed/unparsed completion
cases. This correction changes no memory, retrieval, dashboard, capture,
platform, workflow, release, or GitHub behavior.

### 2026-08-22 source-rebuild atomicity correction

The idempotent/resumable ingestion and full provider-history requirements now
include staged complete-source rebuilds. A rebuild leaves prior current context
untouched while parsing and submitting replacement candidates, then performs
old-record eligibility checks and new policy publication in one Core/SQLite
transaction, with a durable generation/session marker for post-cutover retry.
Focused synthetic regressions cover parser failure, injected
ingestion/policy rollback, cancellation, corrected records, and local-authored
records. Full Ruff and mypy checks pass, and the full Python suite passes 1,065
tests with 4 host-limited symlink skips. The existing exact-candidate
interruption and provider-export receipts remain separate release evidence
requirements.

### 2026-08-22 Memory Truth foundation

This slice adds a Core-owned canonical truth projection after records exist. It
is locally exercised by focused synthetic tests and does not change provider
extraction, retrieval/ranking, dashboard behavior, MCP tools, or release state.

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Canonical memory truth with provenance, reason, time, confidence, sensitivity, status, and conflict visibility | `models.py`; `CoreStore.get_memory_truth`; `GET /v1/context/truth/{record_id}`; `GET /v1/admin/memory-truth`; `tests/unit/test_memory_truth.py` | Implemented locally for current, tentative, superseded, conflicted, and deleted record views. Evidence exposes source/record links, decision metadata, effective/observed/recorded times, confidence, sensitivity, and bounded version history. Public detail remains authorization-first; admin surfaces include non-current records. A replayable append-only decision stream remains partial/deferred |
| Content-free source/observation accounting | `TruthCoverageOut`; `CoreStore.memory_truth_coverage`; `GET /v1/context/coverage`; status projection; focused coverage regression | Implemented locally for source, observation disposition, record status, conflict-group, ingestion completion, and unavailable-source counts. It intentionally does not claim that raw source bytes or provider extraction are complete |
| Stable reprocessing identity without deletion resurrection | migrations `010_memory_truth.sql` and `011_rebuild_tombstone_provenance.sql`; source-rebuild cutover; deletion-barrier regressions | Implemented locally: matching internal source-rebuild tombstones can reapply an untouched automatic archive record under the same ID; ordinary user tombstones block matching archive evidence from becoming current under any new ID; source-reference collisions with different values remain distinct |

### 2026-08-22 privacy ACL boundary repair

| Requirement | Implementation/evidence | Status |
|---|---|---|
| A winning replacement cannot expose new disjoint-private content through the old client ACL | `storage.py::_monotonic_security`; `tests/unit/test_automatic_context_policy.py::test_disjoint_replacement_and_reinforcement_keep_acl_boundaries`; focused temporary-database regression | Implemented locally: overlapping restrictions intersect, disjoint replacement restrictions follow the replacement content, omitted restrictions retain the existing boundary, and disjoint reinforcement retains the current boundary |
| Principal-scoped Memory Truth cannot expose prior canonical projection fields or linked observation evidence outside their ACL | `storage.py::_update_record_from_observation_tx`; `storage.py::_truth_evidence_acl_filter`; `CoreStore.get_memory_truth`; `tests/unit/test_memory_truth.py::test_truth_detail_filters_disjoint_canonical_and_evidence_acl` | Implemented locally: canonical authorization is checked first; a disjoint restrictive correction takes its content-bearing projection fields from the replacement observation; linked evidence is ACL-filtered before the bounded limit. Principal-less Core/local-admin linked history remains intentionally complete; unrestricted records retain their existing behavior |

This repair was validated only with six focused pytest nodeids (6 passed),
`python -m ruff check .`, `python -m mypy packages/allthecontext/src`, and
`git diff --check`. Full pytest, hosted CI, release or publication checks,
network/provider access, live/private data, and macOS work remain outside the
evidence boundary. The focused test environment still emits one unrelated
FastAPI/httpx deprecation warning.

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Frozen Python dependency audit | `pyproject.toml`; `uv.lock`; `scripts/dependency_audit.py`; ADR-091 | The local lock/audit contract remains documented. This integration did not run hosted CI or make release/security acceptance claims. |
| First usable V1 beta contract | `ROADMAP_TO_V1.md`; ADR-053/ADR-075/ADR-086/ADR-088/ADR-089/ADR-092/ADR-095/ADR-096; `acceptance_receipt.py`; both receipt-bundle templates; `STATUS.md`; this matrix | Release publication and exact candidate/client acceptance are outside this integration. The local source retains the Windows/Linux posture and the unsupported-macOS retention decision; no beta receipt is credited here. |
| Post-V1 zero-routine-friction platform direction | ADR-090; `product/ZERO_FRICTION_PLATFORM.md`; `product/ZERO_FRICTION_EXECUTION_PLAN.md`; `product/PRODUCT_REQUIREMENTS.md`; `protocols/CAPTURE.md` | Accepted product and execution direction only. Core remains authoritative; connected text remains inert untrusted data; authorization and lifecycle resolution precede derived work; capability levels prevent L0 integrations from claiming lifecycle hooks; correction, deletion, retention, and purge dependencies close future ATC influence. The public beta.6, merged PR #73 capture foundation, and PR #78 admission contract satisfy Phase 0 / the local admission seam but grant no provider or lifecycle support. Packet E and Packet G are component-complete. PRs #82 and #84 wired CoreService/startup capture-runtime composition and the opt-in Packet E scheduler. Manual-cycle Packet E x Packet F composition remains historical; the 2026-08-26 clean-vault journey now proves the real worker through retry/resume, initial snapshot, incremental update/deletion, and duplicate-free restart without a dashboard. That closes the local developer gap, not complete ZF-007/ZF-008 product exit or packaged/live support. PR #86 merged compilation of admitted records through Packet G and is not ZF-009 product exit. A later stacked local slice forms one caller-declared interaction_preference and is not ZF-010 product exit. Remaining work is Packet G product acceptance, ZF-010 automatic formation, complete Wave 4 E–G (complete Packet H), the packaged source/client journey, and Phase 2. No graph promotion, working-state, learned-retrieval, remote, stable-SDK, provider/client, release, or support claim is credited until separately implemented and accepted |
| Cross-platform Core | `config.py`, `lifecycle.py`, `platform_compat.py`; platform/package smoke tests; retained `macos_acceptance_preflight.py`; ADR-086 | Public-beta floor: Windows 11 x86-64 and Ubuntu 24.04 LTS x86-64 GNOME with working Secret Service/GNOME Keyring; other Linux environments are experimental. macOS source, tests, and the historical preflight remain for portability, but macOS is unsupported and excluded from candidate assets, claims, and acceptance receipts. Exact supported-target clean-machine and final release-SHA receipts remain pending |
| Correct per-user data paths | `platformdirs` configuration and setup/package tests | Implemented |
| Loopback-only default | `CoreConfig`, server CLI, dashboard copy, security tests | Implemented |
| Research supplier provenance and isolation | `research/competitor-intake/memory-systems-intake.v1.json`; Wave 2 manifest; Hindsight provenance and skip receipts; ignored `research/vendor-cache`; adapter/intake/packaging guardrail tests | Official Hindsight source was temporarily cloned at pinned revision `fa69b5b`, statically reviewed, and removed; no supplier package, model, container, script, service, provider, credential, benchmark, copied source, or packaged dependency |
| Observation/disposition/current-context lifecycle | `models.py`, `memory_policy.py`, migration 005, storage transactions, evidence links | Implemented; full local suite and focused policy/storage/API regressions pass |
| Direct-secret pre-ledger boundary | `secret_boundary.py`, migration 008, Core/Relay pre-queue refusal, internal-store pre-write defense, opaque UUIDv4 receipts, startup/export/restore repair and compaction, adversarial byte-scan tests | Implemented locally for direct proposals, batches, corrections/errors, forget/reject/delete redaction, direct Core-store calls, Relay queues before proposal hashing, replay, diagnostics, SQLite/WAL/freelist/FTS, and encrypted export/restore. Detector v4 adds structurally validated compact JWT/JWE and PASETO, selected provider-token prefixes, and contextual bearer/token forms without a broad entropy filter; focused canaries prove rejected values do not reach ledger tables, exports, responses, or logs. Historical external backups and device remanence remain operator-retirement concerns; exact-candidate security acceptance is pending |
| Append-only policy-decision history | decision metadata on observations/current records plus audit/history surfaces | Partial: current metadata is inspectable, but every automatic policy transition is not yet represented as a replayable append-only event stream |
| One-time setup with no routine memory queue | automatic MCP submission plus dashboard Context default/Review removal | Present in the local source; exact packaged fresh-user browser proof is outside this integration. |
| Core-only `automatic-v1` authority | origin assigned by Core; applied/reinforced/tentative/ignored decisions; Relay staged queue receipts | Disposition authority and ACL/Relay tests pass. A-09/B-102 source implementation is integrated: closed `witness:explicit_user_statement` grant, default-false MCP claim, archive-smuggle reduction, and policy decision metadata; exact-client/exact-artifact receipts remain open |
| Explicit user observation becomes current automatically | `add_candidate` policy transaction, MCP response contract, and Codex managed-server approval policy | Approval-free observe-to-later-retrieve E2E and witness unit/integration tests pass locally. Installed Codex CLI 0.144.0 also completed a disposable source-runtime apply/retrieve and post-Core-restart retrieval after the managed ATC server explicitly used Codex's supported `approve` tool policy. A-09 is a documented local-client trust grant rather than cryptographic authorship proof; exact packaged Codex/Claude receipts remain open |
| Tentative/ignored/staged isolation | current-record-only retrieval, staged ingestion, policy tests | Implemented; restart, pre-v5 restore, FTS rebuild, and retrieval-isolation tests pass locally |
| Configurable tentative retention/decay | future versioned policy, deterministic replay, and noncurrent-isolation requirements | Deferred; not implemented or claimed by `automatic-v1` |
| Duplicate reinforcement and deterministic slot conflict | observation links, normalized value matching, explicitness then `observed_at` precedence | Keyed slots implemented. Unkeyed archive lineage (ADR-097) collapses only when a derived subject-only key matches for preference/goal/project/decision/workflow/constraint kinds; bounded preference values (including answer style and dark/light) and literal choice-before-`for` values are excluded from that key. Fixture `b102_chronological_conflicts.json`, reverse-order coverage, and unrelated same-kind independence tests cover the archive path. Kind-only collapse is not used. Generalized multi-slot normalization remains post-V1 |
| Provenance, decision reason/time/version, confidence, sensitivity, validity, record versions, hashes, client permissions | typed models, migration 005, policy/storage round trips | Implemented; source-inclusive/source-free and pre-v5 restore regressions pass locally |
| Optional automatic-decision inspectability | `/v1/admin/observations` exposes disposition, record ID, reason/time/version, source, evidence, and authenticated submitter; Context shows provenance/history; Activity renders the observation stream | Implemented; dashboard/API tests pass locally |
| Immediate correction with preserved history | explicit targeted correction observation and existing record-version lifecycle | Implemented; new/legacy HTTP, MCP, ACL, history, and idempotency tests pass locally |
| Reversible ordinary deletion | history-preserving delete; `restore_record` and admin endpoint restore latest deleted state or a selected historical version, rebuild FTS, version, audit, and replication state | Implemented; Core/API/UI, contiguous-history, and ordered Relay restore tests pass locally |
| Reversible imported-source deletion | migration 006, provenance-bounded source/record deletion membership, admin delete/restore endpoints, dashboard Remove/Undo, duplicate-reimport restoration | Implemented locally; storage/API/UI regressions prove independently deleted records are not resurrected |
| Idempotent/resumable ingestion with atomic policy publication and coverage | `ingestion.py`, `importers.py`, staged observations, raw-first preservation, `finish_ingestion`; retry/resume/coverage tests | Implemented locally: raw bytes are authoritative before parsing; failure/cancellation retains an inert retry source; path parsing uses a reconstructed Core copy; parser-versioned replay publishes no duplicates. Public coverage preserves logical seven-key item accounting with path-specific provider-container/control-member denominator rules, intentional closure for empty generic members, and atomic ordinary JSON validation. The separate content-free ZIP member audit proves raw-member closure without double-counting containers. Source-level `source_terminal_reason` remains separate, so terminal events do not corrupt item totals. Operation-owned cancel/restart paths preserve sanitized prior counts, and ZIP diagnostics escape control characters. Exact-candidate interruption receipts remain pending |
| Generic JSON/JSONL/Markdown import | `importers.py`; importer/security tests | Implemented |
| Raw imports through 2,000,000,000 bytes | `config.py`, `import_boundary.py`, `boundary_canary.py`, `importers.py`, migration 007/009, `import_operations.py`, `source_blobs`/`source_blob_chunks`; database-volume preflight, raw-first preservation, bounded chunks, operation-authoritative unchanged-byte reprocess heartbeats isolated from source telemetry latency, bounded timestamp-only WAL liveness commits, parser-reclassification merge rebinding of operation `source_id` on complete/fail/cancel without complete-canonical downgrade or re-ingest, cancellable preserved-source reconstruction checkpoints with partial-copy cleanup, preserved-source retry trackers initialized at the already-committed byte boundary, boundary-canary-v2 JSONL-safe alignment, closed generic skipped/unparsed accounting, direct source-only telemetry, terminal source state, retry/cancel, migration, copy-integrity, and focused adversarial regressions | A-08 makes the inclusive boundary mandatory. Source-level boundary/recovery machinery, durable import operations, and the corrected deterministic canary contract are implemented. WSL2 first exposed operation delay from serialized source-plus-operation transactions; source now isolates operation-owned heartbeat authority. Exact candidate `4257e40` then reproduced 15/17 over-budget unchanged-byte heartbeat intervals on a qualified 4-vCPU/8-GiB Ubuntu QEMU target, with a 10.196354-second maximum despite correct import data and resource use. Unchanged-byte liveness now updates only the operation timestamp through a 250-ms, WAL-NORMAL telemetry writer; semantic progress and terminal state retain the original lifecycle writer, source-only behavior is unchanged, and non-lock SQLite failures propagate. Candidate `628797d` kept durable retry liveness within 4.936978 seconds but authenticated API receipt reached 5.735102 seconds while direct SQLite stayed within 3.731520 seconds. Operation-owned streaming JSONL parsing now yields one millisecond at existing one-MiB checkpoints so the observer and ASGI loop receive a scheduling turn; auth, durable state, source-only behavior, and response semantics are unchanged. Replacement candidate `7ffb1a4` passed the exact Windows straight/repeat slices but missed the strict cancel-ack deadline because preserved-source reconstruction checked no cancellation until its complete copy; source now checks after every at-most-8-MiB chunk and cleans a partial target on cancellation. Exact candidate source `65612cc` then exposed a no-upload retry progress regression: the preserved source was fully committed, but the first forced retry phase briefly wrote zero committed bytes/percent before restoring the declared size. Retry trackers now initialize their monotonic byte state and emission watermark at the preserved declared size, so the first durable phase cannot regress. A new immutable candidate must rerun the complete Windows journey. A new immutable Linux artifact must prove the frozen five-second gate and run interruption. Allocated non-sparse exact-boundary success on Windows x86-64 and frozen-target Linux x86-64, boundary-plus-one refusal, interruption, resource budgets, SHA, packaged export, and restore evidence also remain open |
| Full local ChatGPT/Claude/Grok history ingestion | `provider_ingestion.py`, `provider_shapes.py`, streaming ZIP/JSON adapters, versioned parser identities (`provider-archives-v2`), closed coverage, staged policy publication, `outcomes`/`record_ids` import response, dashboard provider flow, raw-first recovery, complete-source rebuild from the preserved blob, packaged `--packaged-provider-acceptance` control surface; ADR-069/ADR-088/ADR-097/ADR-099/ADR-100/ADR-106 | All three providers are mandatory under A-11. Frozen fictional shape sets and parser identities are implemented; every import exposes the strict seven-key item map with recognized, excluded, skipped, unavailable, duplicate, failed, and unparsed counts, while source-level failed/cancelled terminal status is separate. Extraction publishes specific durable kinds, requires durable preference evidence, and keeps task-local/adversarial instruction framing inert; sensitivity is classified conservatively. Complete-source rebuild withdraws uncorrected automatic records reversibly and re-extracts without destroying the raw blob or history. Known empty/tool/attachment ChatGPT graph shells now close into excluded/skipped/unavailable while unknown/malformed material remains unparsed and keeps coverage incomplete. Provider conversation wrappers, nested wrappers, and root conversation arrays account for every malformed or unknown entry as unparsed, retain valid siblings, emit content-free structural warnings, and keep `complete` false. Oversized ZIP text members close unavailable and malformed declared-text `.dat` attachments close unparsed only; attacker-controlled ZIP diagnostics are bounded/control-escaped. Packaged acceptance emits content-free stage codes for operation failure, non-complete operation status, and reconcile refusal; synthetic ZIP/classifiable-graph and unknown-residual regressions cover the former ambiguous fail-closed path. ADR-088 binds `CoreService` as a context manager so `CoreStore.close()` always precedes owned-vault `rmtree` on success and exception paths; an `OSError` after close still yields `data_dir_cleanup_failed` / exit 1, and caller-supplied data-dir deletion is unchanged. Privacy-safe nonempty real exports acquired after parser freeze and within 30 days, execution of all three against exact downloaded candidates, and inventory-bound receipts remain open and block beta if any one is missing |
| Bounded ChatGPT attachment inventory and text slice | `importers.py`; synthetic ZIP regressions in `tests/unit/test_provider_ingestion.py`; ADR-105; `docs/protocols/INGESTION.md` | Implemented locally for gated `.dat` identity/hash/raw preservation, manifest/filename/MIME provenance, explicit MIME ambiguity status, unique archive-member identity, exact conversation/message link pairs, strict ZIP bounds, bounded 10,000-pair/64-level/10,000-node linkage scans, and supported text formats `.txt`, `.json`, `.jsonl`, `.csv`, `.md`, `.markdown`. Unsupported binary/document/web/script formats remain explicitly unavailable. This does not claim all `.dat` contents are searchable, does not provide office/PDF/media extraction, and has no real-export acceptance receipt; only structural real-export inspection was performed content-free |
| Structured filtering and FTS5 | retrieval engine; policy-before-ranking and integration tests | Implemented |
| Retrieval usefulness and bounded context packs | `retrieval.py` deterministic query-intent/usefulness rerank; `ContextCompiler`; additive `ContextPackMetadata`; `bench/retrieval_usefulness.py`; synthetic fixture/tests; ADR-116 | Implemented locally: default V3 reranking stays after authorization, temporal, and admissibility gates; ranking combines bounded lexical/query-field coverage with recency, confidence, availability, sensitivity, conflict, provenance, and actionability; bootstrap enforces a 32-record cap and exact character budget; provider-facing pack metadata reports omissions and truthful truncation reasons; 17-case isolated scorecard passes all local gates. No learned retrieval, live-data claim, or release/client/provider acceptance credit |
| Request-bound context-search pagination | `SearchRequest`, `SearchCursor`, Core `/v1/context/search`, per-installation HMAC cursor signing, `tests/integration/test_core_api.py`, ADR-103 | Implemented locally; malformed, negative, oversized, bounded, normal-page, query/filter/page-size mismatch, and cross-principal cursor cases are covered by API tests. The cursor is integrity-authenticated but not encrypted, one-time-use, expiry-bound, or snapshot-consistent |
| Future embedding boundary | shadow-retriever contract plus disabled, rebuild-only 384d exact-scan experiment outside package discovery | Defined; no production embedding dependency or authority |
| Required MCP tools | `mcp_adapter.py`; MCP SDK v2 `MCPServer` and public transport runners; `observed_at` input, automatic disposition/record/reason/time/version output, and explicit reversible `forget_context`; STDIO/Streamable HTTP/OAuth contract tests | Implemented locally; MCP v2 contract, actual unknown-argument rejection, legacy 2025-era handshake, managed STDIO restart, correction, queued-forget, PKCE refresh/revocation, bearer, Core-authority, and the shared 256 KiB hosted Edge request boundary pass focused tests. Ordinary MCP remains L0; lifecycle-aware L1-L3 hooks are not claimed |
| Claude Code explicit-user memory Core contract | `core/app.py`, `ingestion.py`, `models.py`, `security.py`, `storage.py`, `tests/unit/test_claude_code_memory.py`, `tests/integration/test_claude_code_memory_core_api.py`, `docs/protocols/CLAUDE_CODE_MEMORY.md`, ADR-162 | Implemented locally: dedicated remember/correct/reversible-forget routes require a separate principal with exactly `context:propose` plus `witness:explicit_user_statement`; the existing Claude Code read principal remains exactly `context:read`. Core rejects authority fields, assigns origin/sensitivity/availability/ACL/disposition, applies bounded UUIDv4 idempotency, reuses candidate/correction/tombstone machinery, refuses direct secret-like payloads, returns content-free route-validation failures, and has no Relay fallback. Ordinary prompt/model/tool/provider/imported text and MCP metadata are not direct-user evidence; exact-payload native confirmation is the client-side user approval/adoption gate. This is local Core/API evidence integrated with the separate opt-in client boundary; live/private acceptance, release, and macOS claims remain out of scope |
| Claude Code UserPromptSubmit pre-generation client | `claude_code_hook.py`, `claude_code_config.py`, `desktop_setup.py`, `wizard.py`, packaged headless setup in `desktop.py`, focused unit/integration tests, `docs/protocols/CLAUDE_CODE_HOOK.md`, ADR-160/ADR-161 | Implemented as a configured user-level pre-generation client with a distinct exactly-`context:read` principal, separate Claude Code/Desktop choices, OS-keyring vs explicit-development-fallback token handling, two-file rollback/preimage/link-path hardening, strict loopback identity proof, and bounded content-free hook output. This claims only configured Claude Code UserPromptSubmit pre-generation support; it does not claim L1, direct-user capture, durable formation, live/private client acceptance, provider support, dashboard status/repair/uninstall, a product exit, or release acceptance. Ordinary MCP remains L0 |
| Opt-in Claude Code explicit memory command client/config/setup boundary | `claude_code_hook.py`, `claude_code_config.py`, `http_client.py`, `mcp_adapter.py`, `desktop_setup.py`, `wizard.py`, packaged headless setup in `desktop.py`, focused unit tests, `docs/protocols/CLAUDE_CODE_HOOK.md`, ADR-162 | Implemented around `/atc-remember`, `/atc-correct`, and `/atc-forget` personal skills under `~/.claude/skills/.../SKILL.md` and the official `UserPromptExpansion` metadata contract. The feature is off by default, preserves the read principal, provisions a separate exact two-grant write principal, keeps bounded opaque pending state with a content commitment, and treats native exact-payload `confirm=true` as authoritative user approval/adoption before Core mutation. The MCP tool remains model-visible and metadata cannot prove the slash command was personally typed, but direct invocation cannot silently write because the identical confirmation gate applies. The client uses the pending command ID only as `idempotency_key`, retries one ambiguous transport failure with the identical payload/key, reports unresolved outcomes as unknown, preserves unrelated Claude settings/skills transactionally, calls only the narrow Core routes with no Relay fallback, and blocks on missing/revoked Core. Ordinary UserPromptSubmit remains read-only. Nested elicitation from an `mcp_tool` hook is fail-closed but still needs real Claude Code acceptance; no live/private acceptance, macOS work, or product/release exit is claimed. |
| First-party HTTP transport | `httpx2>=2.12,<3`; `http_client.py`, `edge_connection.py`, `edge_acceptance.py`, `sync.py`, `replication.py`, packaged first-run smoke; HTTPX2 exception/trust-store and response-bound regressions | Implemented locally and locked at HTTPX2 2.12.0 with httpcore2/truststore. Production has no legacy `httpx` dependency; old `httpx` is development-only for Starlette `TestClient`. Packaged smoke responses are streamed under a 1 MiB ceiling with content-free refusal. The combined 74-test MCP/HTTP matrix passes locally; hosted Windows/Linux CI has not yet run for this branch |
| One-time local app connection | `client_config.py`, setup wizard, dashboard; Codex/Claude classic and Windows Store/MSIX detection/config tests; autouse disposable client-config/keyring fixture | Implemented locally; this Windows Store install resolves to its package-local roaming config. Installed Codex CLI 0.144.0 accepted the managed STDIO shape and recovered a stopped disposable Core. Tests now fail away from real Codex/Claude config and credential roots; persistent-profile and Claude signed-in exact-artifact receipts remain open |
| Optional administration UI, no memory inbox | `apps/dashboard`; Review route/forms removed, Context default, Activity/provenance, durable import-operation flow, context and source delete/undo, version restore, source rebuild, Context total/pagination/kind/sensitivity/confidence filters without auto-select; search-wrapper `:focus-within` amber outline (ADR-089) | Source now has a focus-dependent search-wrapper indicator, a Python source regression that requires a nonzero non-none wrapper indicator, and a dashboard test that keeps the existing sr-only accessible name. Context search uses the API `total` and cursor pagination instead of a hidden 100-row cap. A static search border is not treated as focus. BETA-P06 has not passed; exact packaged Edge keyboard/focus/error/narrow-width acceptance remains open. Deliberate purge is available via the packaged recovery/admin helper; exact downloaded-artifact administrator receipts remain open |
| Approval-free reproducible demo | `scripts/demo.py`, `tests/e2e/test_demo.py`; automatic finish-to-retrieve, restart, correction/delete, revocation, encrypted restore | Present in the local source; this integration did not run hosted matrices or final release-SHA replay. |
| Portable export/restore | encrypted export/dashboard download, contributor CLI restore tests, packaged recovery/admin helper/mode (`recovery_admin.py`, Windows helper, Linux console main binary); retained Mac helper code; separately hashed source-chunk entries with complete-source reconstruction checks | Existing round trips and packaging integration pass. Exact downloaded-artifact stopped-Core restore/purge receipts on supported Windows and Linux remain open (BETA-D03); macOS is outside the release scope |
| Locking, shutdown, restart | lifecycle locks, managed adapter self-heal with one bounded 30-second one-file startup window, packaged first-run smoke; idempotent `CoreStore.close()` / `CoreService` context manager (ADR-088) | Implemented in local source; focused verification is scoped to this integration and no hosted, exact-artifact, or release-SHA acceptance is claimed. |
| OS credential abstraction | `credentials.py`, transactional desktop/client configuration, keyring acceptance script, platform fault-injection tests; packaged first-run smoke uses explicit isolated development-file credentials only | Normal setup fails closed without protected OS storage; plaintext development files require deliberate opt-in; managed configs omit bearer tokens when the OS store is used; failed storage/config writes revoke new principals, remove credentials, and restore prior config bytes. Packaged first-run smoke asserts the isolated development store and does not stand in for real OS credential acceptance; failure diagnostics are content-free and the disposable work tree is always removed. Exact-package real Windows Credential Manager and supported Linux Secret Service receipts remain pending; the Mac adapter is retained but unsupported |
| Safe Core response sinks | browser handoff data-attribute encoding, constant nonce-protected handoff script, inert acceptance parser, sanitized integration configuration failures, focused security regressions; ADR-064/ADR-070 | Product responses are implemented locally: request-derived dashboard targets and browser capabilities are data rather than executable JavaScript, and integration parser exceptions cannot disclose raw paths, credentials, or personal configuration material. The acceptance extractor binds the handoff nonce to the exact response CSP and rejects external `src`, extra executable markup, inert/ambiguous handoffs, and non-production storage/targets; focused adversarial regressions pass |
| Exact browser handoff and dashboard hygiene | packaged same-origin SVG favicon, bundled-serving regression, real browser P06/S05 receipt; ADR-009/ADR-064/ADR-070 | Source correction implemented after an exact Windows Edge handoff exposed the implicit `/favicon.ico` JSON 404. A clean committed local Windows package passed the focused real Edge P06/S05 replay with zero unexpected console/page errors, no external request, and the packaged favicon. Independent parser hardening is integrated and its exact production handoff/CSP probe passes. A rebuilt official downloaded release candidate remains required. BETA-S05 follows the frozen expiry/non-replay/referrer/cache/current-navigation/session termination/revocation boundary and does not impose forensic byte erasure on an already consumed ticket |
| Cross-platform source CI | `.github/workflows/ci.yml` source, dashboard, and supported native-package matrices; retained `macos_acceptance_preflight.py` as historical/source portability code | The hosted matrix covers only supported Windows and Ubuntu runners. The three Mac job contexts and ordinary Mac preflight were removed; retained Mac source and historical evidence create no support claim or receipt. Final release-SHA source health and supported clean-machine receipts remain pending |
| Supported desktop packaging | Windows installer and Linux portable archive; version-matched recovery helper/mode; bounded native-tool failure diagnostics and a single 30-second managed-Core startup window; packaged first-run smoke startup-key cleanup (`remove_smoke_windows_startup_key`); retained Mac packaging code under ADR-086 | Windows and Linux packaging/recovery surfaces are integrated. The official candidate matrix, inventory, release notes, and publication verifier accept only Windows x86-64 and Linux x86-64. Mac app/DMG code and historical packaging evidence remain in source but create no consumer asset or support evidence. Exact supported release artifacts and real-machine/downloaded-artifact receipts remain pending |
| Signed community updates | Ed25519 manifest/keyring, update-state, rollback, and content-free verification code | Local update surfaces remain in source, but this integration did not validate publication, hosted workflows, channel promotion, or exact downloaded artifacts. |
| Stable release trust/channel path | candidate workflow accepts stable versions | Open: key selection, site builder, client endpoint, publish/promotion workflow, migration rehearsal, backup, and recovery remain beta-specific or absent |
| Exact-SHA reproducible candidate composition | `exact_source_gate.py` (canonical `.github/workflows/ci.yml` only; current eight required jobs with bound run_id/head_sha; primitive matrix-evidence recompute), `release_candidate.py` inventory schema + required checksum sidecars, authenticated release-list/numeric-ID draft resolver, receipt inventory-declared digests, dependency-closed locked install/parity scripts, checksums, provenance, SPDX SBOM, ADR-059/ADR-068/ADR-096 adversarial tests | The published beta.6 source historically passed its canonical 11-job CI and three-job CodeQL sets; the current exact-source contract has eight required CI jobs after removing the three unsupported Mac contexts. Draft operations still require numeric REST asset IDs. Published-state validation safely tolerates opaque `gh release view` GraphQL IDs without treating them as REST authority. Broader certification receipts remain separate from this closed lean publication identity |
| Repository and release security baseline | local dependency/security configuration and source-side guardrails; ADR-065/ADR-080/ADR-087 | Hosted repository settings, CI, CodeQL, release environments, and exact candidate scans were not revalidated in this integration and receive no acceptance credit here. |
| Public support and launch-watch sequencing | `SUPPORT.md`, `docs/KNOWN_ISSUES.md`, `SECURITY.md`, `docs/operations/RUNBOOK.md`, README links, ADR-075/ADR-092 | Local support and safety documentation remain in scope; publication, channel/download smoke, launch-watch, and exact-client acceptance are outside this integration. |
| No third-party V1 runtime | no Edge UI/onboarding/status call/background worker; Edge publication workflow and Render templates removed; ordinary Core Edge/Relay operation routes and CLI commands removed or tombstoned; residual cleanup isolated under legacy-edge surfaces only | Implemented for the supported Core product surface (`BETA-S04`/`B-103`); exact packaged candidate matrix proof and publication remain open |
| Direct-Core mobile model | integration API/dashboard/architecture state Core-online requirement | Explicitly post-V1: the first usable beta is same-device only and has no supported pairing/transport/client acceptance claim |
| No automatic public exposure | loopback default; dashboard warning; acceptance gate | Implemented |
| Legacy `always_available` compatibility | schema and old records retained; new applied context uses `core_available`/`local_only` and labels old records legacy | Implemented |
| Legacy review-data migration | migration 005 maps approved/rejected to applied/ignored and startup reevaluates eligible staged rows under `automatic-v1` | Implemented; partial-migration restart, pre-v5 duplicate restore, and idempotency regressions pass locally |
| Remote Edge scoped forwarding authorization | experimental `edge_connection.py` compatibility path enforces Core-approved `context_scopes` on direct fetch, search, and bootstrap records and scrubs filtered aggregates; bootstrap metadata is reconciled to final items with strict count invariants and bounded Core-selection suppression aggregates; `tests/security/test_edge_forwarding.py` covers empty, wildcard, matching, out-of-scope, filter, and envelope paths | Implemented as defense in depth on residual experimental code; ordinary Core product routes that would invoke it are removed/tombstoned by B-103; focused synthetic correctness evidence only |
| Relay remains queue/projection only | Relay MCP returns staged receipts; Core evaluates dequeued observations; signed record events originate at Core | Authority tests pass; ordinary Core CLI no longer exposes `sync` or `serve-relay`; Relay modules remain for residual/compatibility tests only |
| Legacy Edge cleanup without normal operation | isolated `/v1/admin/legacy-edge` and `atc legacy-edge` status/decommission/forget; no automatic worker; decommission refuses when no residual paired Edge exists | Implemented with negative API/CLI/process/network proofs; exact packaged candidate artifact proof remains open |
| Frozen Retrieval V2 comparator | `retrieval_contracts.py`, pinned fixture hashes/ranking fingerprints, foundation harness | Implemented; comparator identity `70a4808` |
| Applied/current policy before time/relevance | authorization-only selector, current-record eligibility, temporal IDs, ranker-candidate-scoped FTS, boundary tests | Baseline and automatic-disposition migration/isolation verification are integrated; final candidate replay pending |
| Current and `as_of` retrieval | UTC interval sidecar, request/MCP/CLI fields, DST/offset/restart tests; Core catalog search requires `context:read` before returning current or historical content while non-content status remains independently gated by `context:status`; exact post-policy totals and cursor pages remain separate from bounded bootstrap evidence retrieval | Implemented locally; focused current-worktree regressions cover synthetic >100-match exact-total/page, authorization/filter-isolation, status-only monitoring, and search denial. No historical three-OS or hosted result is reasserted here. |
| Deletion/purge resurrection barrier | authoritative terminal facts, purge tombstones, stale-sidecar recovery, pre-removal export restore test | Covered by local focused regressions; no historical three-OS or hosted result is reasserted for this checkout. |
| Weighted bounded FTS5 | `lexical_v3.py`; weighted columns, bounded evidence search, complete authorized catalog enumeration under the 50,000-candidate hard cap, exact/OR/prefix caps, Unicode/case/tokenizer and secure-delete tests | Implemented locally; catalog search is exact over the post-policy set while bootstrap/context compilation retains the 100-record evidence bound |
| Task admissibility | deterministic numeric factor gate after hard policy/time, fail-open sparse evidence, shadow-only learned interface | Implemented locally; bounded precision improves without exact Recall@5 loss |
| Safe retrieval diagnostics | closed reason codes and numeric/boolean aggregates; admin-only returned-ID explanations | Implemented; content/unauthorized-ID exclusion tests |
| Retrieval V3 benchmark gate | foundation fixtures plus integrated 1k/10k quality, latency, storage, migration/restart/restore checks; bounded failed-gate report; ADR-083 evidence boundary | Production CLI remains fail-closed at 10k warm p95 below 150 ms on comparable hardware. Shared-host 100-record pytest is functional evidence only; deterministic tests enforce the unchanged threshold and reject invalid/missing/mixed-profile latency evidence. Earlier source/tests/packages were observed on three OSes at `67dd11c`; a new comparable-hardware CLI run is required for current latency evidence |
| Set-level marginal context selection | `set_selection.py`, `ContextCompiler` wiring, compatibility/diversity/conflict/support/mandatory/budget fixtures, `tests/unit/test_retrieval_high_cardinality.py` | Implemented locally; standalone set-selection gates and combined semantic coverage remain passing; high-cardinality compiler regression preserves feasible primary results and bounded preferences across 77 preferences, 20 relevant records, ten generic queries, a 4,000-character budget, no-match, tight-budget, ACL/temporal/sensitivity, exact-accounting, caller-ranked duplicate relevant records, fixed mandatory survivor/conflict authority with exact prepass/final identity, alternate-primary evidence support, 1007/905 evidence-over-overflow boundaries, large-evidence ordering, infeasible-evidence fallback, and preference-input permutation cases |
| Synthetic retrieval usefulness eval | ADR-116; `bench/retrieval_usefulness.py`; sanitized fixture; isolated public-API vault; scorecard baseline; `tests/unit/test_retrieval_usefulness.py`; `tests/unit/test_retrieval_high_cardinality.py` | Developer-facing only: synthetic usefulness cases plus the bounded high-cardinality regression cover current facts, stale/conflict/withdrawn exclusion, sensitivity, provenance, budget, provider packaging, preference starvation, fixed-slot authority and duplicate survivors, alternate-primary overflow support, applicable evidence ordering and evidence-over-overflow boundaries, infeasible-evidence fallback, no-match reserve behavior, and exact disjoint/overlapping bounded-pool metadata unions. The harness refuses live Core data dirs and grants no release or client acceptance credit. Production ranking, ingestion, schema, MCP, and dashboard Context are unchanged |
| Optional local dense shadow | disabled in-memory 384d exact-scan experiment, bounded tests, authorization-first filtering | Implemented as research only; 10k p95 `400.294955 ms` misses `150 ms`; real model/semantics unexercised |
| Source-evidence retrieval research | sanitized imported-chat fixtures; lexical passage and deterministic token-MaxSim benchmark/report | Implemented as research only; 64/256 recall and coverage `1.0`, diverse redundancy zero; neural path unexercised |
| Hybrid AI-memory reliability program | ADR-042; `docs/research/ATC_MEMORY_RELIABILITY_ARCHITECTURE.md`; external-baseline, Memory Plane, Intent/Consequence Plane, outcome-closure, and benchmark contracts | Research direction only; no external engine, new schema, working/episodic/procedural runtime, checkpoint ABI, or learned component implemented |
| Consequence-closed context | `docs/research/CONSEQUENCE_CLOSED_CONTEXT.md`; consequence contracts, capsules, target invalidation, memory-constraint tokens, and ConsequenceBench | Research only; explicitly not the complete memory product and not an enforcement or client-conformance claim |
| Memory Lab M0 adapter and task-metric ABI | `memory_lab.py`, `memory_lab_baselines.py`, `bench/memory_lab.py`, unchanged sanitized M0 fixture, separate Wave 2 control config, identifier-safe JSON/Markdown reports, and `test_memory_lab.py`; read-only authorized snapshot, v1 adapter ABI, additive task budget, report v2, abstention, sufficiency, forbidden output, budget, disclosure, determinism, latency/storage/cost, failure, and evidence-disposition contracts | Coordinator-reproduced bounded research: stable current-state log advances with `1.0` success/recall and zero forbidden output on 7 objects/5 tasks/20 repeats; current ATC is `0.8`/`0.9`/zero; retrieval-only, potentially fixture-aligned, and not implementation acceptance |
| AI-memory evaluation program | `docs/research/ATC_MEMORY_EVALUATION_PROGRAM.md`; specification/fixtures/tests; `memory_reliability_lab.py`; partial-E01 fixture/runner/report/tests | E01 specification covers 18 scenarios; coordinator-reproduced 6-scenario reference slice has governed 6/6, append-log 0/6, no-memory 1/6, and distinct four-rule ablation failures; fixture/rule co-design and no production Core/external/action execution are explicit |
| Governed external Hindsight boundary | `bench/hindsight_supplier_adapter.py`; pinned provenance and `not_executed_dependency_and_egress_gate` receipt; fake-client tests | Adapter declaration/translation/cleanup contract reproduced locally; no `L3` supplier result, benchmark score, supplier runtime, or production dependency; real execution requires a new gate |
| Governed independent Memory Lab waves | ADR-044/ADR-045; governance document; completed Wave 2 manifest; integrated Wave 2 result; machine-checked governance tests | Wave 2 complete with five visible worktree cells, coordinator-only integration, two `L2` deterministic synthetic results, two `L0` research reports, and one preserved supplier skip; no production promotion or real-user evidence |
| Wave 3 falsification program | ADR-046/ADR-047; completed Wave 3 manifest; integrated result; B01/O01/P01/E01b/M2 harnesses, fixtures, reports, and tests; metadata-only external-artifact intake | Complete with coordinator-only integration and 43 focused reproduction tests: B01 bounded configuration killed, O01 static winner held, P01 automatic durability held, E01b 6 narrow passes plus 6 unsupported/not-exercised semantics, M2 narrowly retained, and MPBench execution denied pending a separate quarantine cell; no production or external benchmark promotion |
| Evidence-Compiled Memory research contract | ADR-047/ADR-049; Wave 3 and Wave 4 integrated results; M2 sealed projection; M3 influence closure; M1 observable-use ledger; E02 production gap receipt | Research direction only: M3 and M1 retained at coordinator-reproduced `L2`; five required Core semantics remain unsupported and one is not exercised; no schema, runtime, external system, or solved-memory claim |
| Wave 4 closure and use-ledger program | ADR-048/ADR-049; completed Wave 4 manifest; integrated result; independent F02 oracle/review; M3/E02/M1 harnesses, fixtures, reports, and tests | Complete evidence-only execution: 49 focused tests; M3 15/15 and M1 16/16 frozen attacks pass with all hard-safety counts zero; E02 records five `UNSUPPORTED` and one `NOT_EXERCISED`; coordinator-only integration and no production promotion |
| Evidence-Compiled Prospective Memory hypothesis | ADR-049; Wave 4 integrated result; event-contingent transaction and frozen first-experiment design | Research proposal only: typed cue before disclosure, minimal current evidence, negative guards, dependency closure, action ceiling, and observable outcomes must beat a simple deterministic scheduler under non-compensable lifecycle and authority gates |
| Exact-candidate repository security | `repository_security.py`, exact committed-tree/history CLI binding, large ZIP/private-key and deleted-archive regressions, binary-vs-text ZIP absolute-path regressions; ADR-061/ADR-067 | Implemented with bounded history and streamed native-package/ZIP-member ceilings. P0 key/credential/raw-context scanning remains active for every ZIP member; P1 developer-home detection applies to human-readable members rather than incidental upstream roots in compiled extensions. The 36 exact POSIX artifact files from failed run `30200529010` rescan clean locally after this correction, but the release workflow has not been rerun and tar.gz/DMG content expansion is not claimed |
| Personally framed sensitivity and forwarding boundary | `memory_policy.py`; automatic context policy tests; Core Edge forwarding tests; ADR-102 | Implemented locally: partner/residence, HIV/health, and mortgage/loan statements are conservatively classified as `sensitive`, forced to `local_only`, and excluded from Core forwarding; unframed technical/general controls and highly sensitive precedence are covered. The heuristic is not an exhaustive semantic privacy detector |

| Memory Truth final review blockers | migrations 010/011/012/013/014; `storage.py`; `models.py`; `importers.py`; `export.py`; `recovery_admin.py`; Memory Truth/storage/export/recovery/Core API/provider tests; ADR-112/ADR-113/ADR-114/ADR-115 and the restore-boundary decision | Implemented locally in this integration: comment-aware restart-safe migration recovery; unbound/portable rebuild provenance downgraded to ordinary deletion barriers; public withdrawal fail-closed; trusted tombstones bound to the exact finished archive session, generation, and source marker with source/session/accessibility, metadata, stable-key, hash/version, and explicit local-mutation checks before reapply; schema-14 typed canonical user-action evidence and typed portable-ledger validation, with generic record-version rows retained only for explicit legacy compatibility; startup/migrate repair of any missing schema-014 typed-action columns and its typed-action unique index when migration 014 is already recorded; destination-local isolated-restore carry-forward; source-typed legacy inference; actual-insert restore/carry-forward counts; and already-current restore barriers; approval overrides recompute candidate and canonical keys from final identity-bearing values; and truth pagination uses SQL page/count selection plus page-scoped, row-limited set prefetch without read-time integrity rebuilds. Focused checks are local and scoped; no full-suite, hosted-CI, live-Core, private-export, or final-acceptance claim is made here. |

## Deferred by the V1 boundary

- hosted Edge/Relay deployment and offline mobile replicas;
- third-party hosting/provider setup;
- multi-master synchronization, CRDTs, family accounts, and multi-tenant SaaS;
- live location, heart rate, wearables, and emergency response;
- production vector embeddings; and
- automatic secure remote-Core exposure until device pairing and encrypted
  transport are designed and accepted.

### 2026-07-28 import-operation liveness amendment

Queryable import-operation liveness now maps to timestamp-only WAL commits that
bypass the Python lifecycle lock and, only for lightweight operation liveness,
run at one tenth of the public five-second budget. The async status dependency
uses a dedicated single-worker with a persistent bounded read-only/query-only
WAL connection; each poll joins current non-revoked registration state and the
operation in one fresh statement. A process-keyed, worker-local HMAC cache
avoids repeated PBKDF without caching raw tokens or skipping durable revocation
checks. Only this high-frequency status observer omits per-poll durable
`last_used_at` activity writes; other routes retain ordinary authentication
activity semantics. Its worker is recreated for each sequential application
lifespan. Regressions cover cross-thread writer contention, source-only cadence,
async routing, cache mismatch/revocation/non-persistence, executor-thread
cleanup, and authorization-before-not-found ordering. A content-free WSL2
timing discriminator completed the exact 2,000,000,000-byte straight import
with maximum unchanged-byte `updated_at` intervals of 3.590 seconds by direct
SQLite observation and 4.774 seconds through the authenticated API. This
supports the source behavior only. Qualified QEMU and rebuilt exact-candidate
proof remain required.

### 2026-07-30 authenticated receive-liveness amendment

Exact candidate `628797d` produced a content-free, 1,338-record fsynced trace
for the qualified 2,000,000,000-byte cancel/no-upload retry. The authoritative
operation timestamp advanced within 4.936978 seconds and direct SQLite
timestamp/receipt gaps were 3.701321/3.731520 seconds, while authenticated API
receipt reached 5.735102 seconds. API request latency reached 3.428642 seconds
and first delivery lagged direct visibility by 3.986875 seconds. This closes
the durable-freeze hypothesis and maps the remaining B-105/BETA-D01 gap to
same-process API scheduling and delivery during CPU-heavy streaming JSONL
parsing.

`importers.py` now gives only operation-owned streaming JSONL parses a
one-millisecond scheduler handoff at the existing one-MiB progress checkpoints.
The durable operation row, timestamp-only WAL writer, authenticated joined
observer, revocation and scope checks, NotFound ordering, and response schema
are unchanged. The focused adverse-scheduler regression exercises the actual
cached authenticated observer and durable joined SELECT: before the handoff it
could not start until the roughly one-second bounded parse ended; with the
handoff it starts within 0.6 seconds and separately bounds auth/SELECT and JSON
serialization. A new immutable candidate must prove the frozen five-second
gate on the qualified Linux retry and then run the still-missing interruption
slice. No candidate acceptance is claimed from source tests.

### 2026-07-30 Windows cancellation amendment

Exact candidate `7ffb1a4` passed the Windows 2,000,000,000-byte straight import
and repeat but did not reach durable `cancelled` before the strict five-second
deadline. The cancel HTTP request returned and authenticated observation plus
timestamp-only liveness remained responsive, separating intent return and
observer delivery from worker acknowledgment. The worker was reconstructing
the preserved source under a `parsing` phase, and that full bounded-memory copy
had no cancellation checkpoint.

Reprocess now passes `ImportProgressTracker.check_cancelled` to the storage copy
helper, which invokes it after every stored chunk and removes any partial
target if the callback raises. A production-path controlled-copy regression
fsyncs HTTP-return, durable-terminal, and worker-quiescence timing before
assertions; the source fix reduces those clocks from
0.021 seconds / beyond the scaled 0.75-second acknowledgment bound /
1.560 seconds to 0.022 / 0.113 / 0.135 seconds. A second regression proves
partial-copy cleanup. The frozen five-second acknowledgment and 30-second
quiescence contracts are unchanged. Candidate `7ffb1a4` is invalidated, and a
new exact Windows artifact must rerun the entire journey; this source result is
not acceptance evidence.

### 2026-08-01 Windows repeat reconstruction-liveness amendment

Candidate descriptor
`b00297d19080d0a3252a48fe5d7ac3ad78d5395909612f86eb2ef1f2e851bc16`
on source `905efe5631ebf2fee77fafa5d8694f77df17b8bb` completed straight and
repeat data work but failed the repeat liveness gate. Consecutive durable
top-level timestamps during unchanged-byte `parsing` were 5.448395 seconds
apart; direct observations received them 5.447142 seconds apart. No receipt
was emitted.

The operation-owned repeat path exposes `parsing` before preserved-source
reconstruction and again before parser entry. A scaled, content-free
production-path regression reproduced the uncovered copy interval on
untouched `905efe5`: first successful liveness touch arrived 0.964490 seconds
after reconstruction start against a less-than-0.4-second gate, while
idempotent candidate identity remained correct.

`importers.py` now keeps the per-chunk cancellation check and adds a
one-millisecond handoff only when the tracker owns operation liveness.
`test_repeat_copy_yields_to_operation_heartbeat_under_cpu_pressure` covers the
production repeat path; the source-only negative test proves no added pause,
and existing cancellation/partial-copy tests retain fail-closed coverage.
Cadence, durable semantics, and the frozen five-second threshold are unchanged.
A new immutable candidate must rerun the complete Windows journey.

### 2026-08-02 source-blob finalization-liveness amendment

Replacement candidate source 7afc46b completed one Windows boundary
straight/repeat probe within budget, but a fresh evidence-complete straight run
failed closed. At unchanged 2,000,000,000 committed bytes, the maximum
top-level operation updated_at interval was 6.325973 seconds through the
authenticated API and 6.253638 seconds through direct SQLite. The import still
completed with exact hash, five candidates, closed coverage, clean SQLite, and
zero foreign-key violations. No receipt was emitted and the remaining journey
stopped.

The operation tracker had a liveness sink from construction, but its background
scheduler did not start until reprocess_source entered parsing. Source-blob
promotion therefore depended only on synchronous phase/chunk writes. A bounded
source-level 2 GB discriminator isolated the remaining lock behavior:
chunk-layout validation held SQLite's writer transaction for 1.253978 seconds;
independent timestamp-only touches returned busy while it was open and first
succeeded 0.013890 seconds after commit.

ImportOperationService now starts the operation-owned scheduler before staging
and closes it in a finally block across success, cancellation, failure, and
process-after-false return. CoreStore retains its Python lifecycle lock across
finalization but validates ordered chunk indexes and total bytes in a deferred
WAL read transaction. Timestamp-only liveness bypasses that Python lock and can
write concurrently; a fresh bounded immediate transaction rechecks immutable
blob fields before setting complete. Regressions prove the long validation
scan no longer owns SQLite's writer slot and that pre-parser promotion advances
and closes its background heartbeat. Integrity, phase/byte monotonicity,
thresholds, and source-only behavior remain unchanged. A replacement immutable
candidate must rerun the complete exact-artifact journey.

### 2026-08-08 nonterminal-operation durability amendment

Exact candidate source `4ab235d` twice completed the qualified Linux x86-64
2,000,000,000-byte straight import with exact source identity, 239 chunks, five
candidates, closed coverage, clean SQLite integrity, and zero foreign-key
violations. Both runs nevertheless failed BETA-D01 liveness at unchanged full
committed bytes during `processing`/`parsing`: durable top-level timestamp gaps
were 5.918573 and 5.332539 seconds, with independent authenticated API and
direct-SQLite receipt gaps also above the frozen five-second budget. No receipt
was emitted and later slices did not run.

Standalone full-source streaming, reconstruction, and complete JSONL parsing
kept a separate 20-millisecond observer schedulable. The remaining source path
generated the phase timestamp before committing it through the default FULL
WAL connection; readers retained the prior committed row while that flush held
SQLite's writer slot, so the fail-fast timestamp-only heartbeat could not
publish a newer row.

`CoreStore.update_import_operation` now routes only explicit nonterminal
progress to a serialized WAL-NORMAL transaction. The path keeps the Python
write lock, ordinary SQLite busy budget, atomic row validation, and monotonic
bytes. Preflight, cancellation intent, clear/error changes, result data,
completion, terminal states, source/blob authority, and all unrelated writes
remain FULL-durable. Focused regressions verify NORMAL/WAL configuration and
adversarial FULL routing. The five-second requirement is unchanged; a new
local source wheel passed a straight-only run in the same qualified guest with
a 0.780195-second maximum durable timestamp gap and 0.786998/0.800204-second
maximum API/direct receipt gaps. It emitted no receipt. A new immutable Linux
candidate must pass the complete D01 matrix.
