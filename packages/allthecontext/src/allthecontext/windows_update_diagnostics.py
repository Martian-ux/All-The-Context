"""Closed, content-free diagnostics for the packaged Windows update child."""

from __future__ import annotations

import re
from typing import Final

UPDATE_FAILURE_REPORT_MAX_BYTES: Final = 4 * 1024
UPDATE_FAILURE_REPORT_STATUS: Final = "failed"
UPDATE_FAILURE_REPORT_FIELDS: Final = frozenset({"attempt", "code", "phase", "status"})
UPDATE_FAILURE_REPORT_PHASES: Final = frozenset(
    {
        "build_identity",
        "component_bootstrap",
        "component_digest",
        "component_presence",
        "entrypoint_registration",
        "internal",
        "report_write",
    }
)
SAFE_BOOTSTRAP_FAILURE_CODES: Final = frozenset(
    {
        "bootstrap_backup_invalid",
        "bootstrap_backup_source_invalid",
        "bootstrap_busy",
        "bootstrap_cleanup_untrusted",
        "bootstrap_component_set_invalid",
        "bootstrap_component_set_too_large",
        "bootstrap_component_too_large",
        "bootstrap_copy_invalid",
        "bootstrap_cutover_failed",
        "bootstrap_identity_changed",
        "bootstrap_install_root_changed",
        "bootstrap_install_root_untrusted",
        "bootstrap_journal_invalid",
        "bootstrap_journal_too_large",
        "bootstrap_journal_untrusted",
        "bootstrap_journal_write_failed",
        "bootstrap_retry_required",
        "bootstrap_rollback_requested",
        "bootstrap_source_changed",
        "bootstrap_source_invalid",
        "bootstrap_stage_invalid",
        "bootstrap_target_invalid",
        "bootstrap_target_substituted",
    }
)
PACKAGED_BOOTSTRAP_FAILURE_CODES: Final = frozenset(
    {
        "component_bootstrap_source_invalid",
        "component_bootstrap_core_probe_failed",
        "component_bootstrap_transaction_failed",
    }
)
UPDATE_FAILURE_REPORT_CODES: Final = frozenset(
    {
        "build_identity_invalid",
        "component_bootstrap_failed",
        "component_digest_invalid",
        "component_presence_invalid",
        "entrypoint_registration_failed",
        "internal_failure",
        "report_write_failed",
    }
    | SAFE_BOOTSTRAP_FAILURE_CODES
    | PACKAGED_BOOTSTRAP_FAILURE_CODES
)
UPDATE_FAILURE_ATTEMPT_PATTERN: Final = re.compile(r"^[0-9a-f]{32}$")
JOURNAL_DIAGNOSTIC_PHASES: Final = frozenset(
    {
        "abort_requested",
        "binary_replaced",
        "committed",
        "cutover_started",
        "diagnostics_passed",
        "health_passed",
        "prepared",
        "rollback_requested",
        "rolled_back",
        "rolling_back",
        "waiting_for_parent",
    }
)
JOURNAL_DIAGNOSTIC_CODES: Final = frozenset(
    {
        "application_phase_invalid",
        "application_state_mismatch",
        "application_state_untrusted",
        "application_untrusted",
        "apply_report_invalid",
        "binary_cutover_deadline",
        "binary_cutover_failed",
        "child_failure_report_invalid",
        "child_failure_report_missing",
        "child_zero_report_missing",
        "child_zero_target_digest_mismatch",
        "component_manifest_invalid",
        "cutover_failed",
        "database_backup_invalid",
        "diagnostics_failed",
        "health_check_failed",
        "install_target_untrusted",
        "journal_component_manifest_invalid",
        "journal_core_invalid",
        "journal_digest_invalid",
        "journal_error_invalid",
        "journal_identity_invalid",
        "journal_invalid",
        "journal_mcp_invalid",
        "journal_path_invalid",
        "journal_process_invalid",
        "journal_recovery_invalid",
        "journal_shape_invalid",
        "journal_time_invalid",
        "journal_untrusted",
        "journal_value_invalid",
        "journal_version_invalid",
        "metadata_invalid",
        "metadata_too_large",
        "metadata_unreadable",
        "metadata_untrusted",
        "parent_exit_timeout",
        "recovery_authority_invalid",
        "recovery_authority_missing",
        "recovery_authority_unavailable",
        "recovery_file_unreadable",
        "recovery_helper_untrusted",
        "recovery_identity_invalid",
        "replacement_process_failed",
        "replacement_untrusted",
        "rollback_component_invalid",
        "rollback_copy_invalid",
        "rollback_requested",
        "rollback_retry_required",
        "rollback_source_changed",
        "runonce_override_invalid",
        "startup_state_invalid",
        "startup_state_mismatch",
        "startup_state_missing_with_transaction",
        "startup_state_reset_failed",
        "startup_state_untrusted",
        "transaction_report_invalid",
        "transaction_report_untrusted",
        "trusted_file_invalid",
        "windows_required",
    }
    | UPDATE_FAILURE_REPORT_CODES
    | SAFE_BOOTSTRAP_FAILURE_CODES
    | PACKAGED_BOOTSTRAP_FAILURE_CODES
)


def valid_update_failure_attempt(value: object) -> bool:
    """Return whether a child-attempt binding has the fixed opaque shape."""

    return isinstance(value, str) and UPDATE_FAILURE_ATTEMPT_PATTERN.fullmatch(value) is not None
