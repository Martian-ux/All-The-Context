"""Content-free provider import proof executed by the shipped desktop binary."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .config import CoreConfig
from .core.service import CoreService
from .models import CLOSED_COVERAGE_KEYS, CoverageReport, ObservationDisposition
from .provider_ingestion import PARSER_VERSION, normalize_provider
from .provider_shapes import (
    PARSER_IDENTITIES,
    reconcile_closed_coverage,
)
from .storage import InvalidStateError

_MANDATORY_PROVIDERS = frozenset({"chatgpt", "claude", "grok"})
_OUTCOME_KEYS = frozenset(item.value for item in ObservationDisposition)
_SAFE_EXPORT_FORMAT = re.compile(r"[a-z0-9_.+-]{1,120}")
_SAFE_EXTENSIONS = frozenset({".zip", ".json", ".jsonl", ".md", ".markdown", ".txt"})


def _make_temp_data_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="atc-provider-acceptance-")).resolve()


def _failure(code: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "failed",
        "operation_status": "failed",
        "error_code": code,
        "content_free": True,
        "aggregate_parser_version": PARSER_VERSION,
    }


def _write_report(path: Path, payload: dict[str, Any]) -> bool:
    """Create a report once so a stale successful report is never overwritten."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        return True
    except OSError:
        return False


def _safe_display_name(provider: str, export_path: Path) -> str:
    extension = export_path.suffix.casefold()
    if extension not in _SAFE_EXTENSIONS:
        extension = ".bin"
    if extension == ".markdown":
        extension = ".md"
    return f"{provider}-acceptance-export{extension}"


def _coverage_report(value: object, complete: object) -> CoverageReport:
    if not isinstance(value, dict):
        raise ValueError("coverage is not an object")
    # Keep packaged acceptance on the same strict schema and completion
    # invariant as the Core ingestion contract. In particular, do not coerce
    # booleans, floats, strings, or out-of-range integers into counts.
    return CoverageReport.model_validate({"closed_coverage": value, "complete": complete})


def _successful_payload(result: dict[str, Any], provider: str) -> dict[str, Any]:
    detected_provider = result.get("provider")
    parser_identity = result.get("parser_identity")
    export_format = result.get("export_format")
    if detected_provider != provider:
        raise ValueError("provider identity mismatch")
    if parser_identity != PARSER_IDENTITIES[provider]:
        raise ValueError("parser identity mismatch")
    if not isinstance(export_format, str) or _SAFE_EXPORT_FORMAT.fullmatch(export_format) is None:
        raise ValueError("export format identity is invalid")

    coverage_value: object
    coverage_complete_value: object
    coverage = result.get("coverage")
    if isinstance(coverage, dict):
        coverage_value = coverage.get("closed_coverage")
        coverage_complete_value = coverage.get("complete")
    else:
        coverage_value = result.get("closed_coverage")
        coverage_complete_value = result.get("complete")
    coverage_report = _coverage_report(coverage_value, coverage_complete_value)
    reconciled = reconcile_closed_coverage(
        {
            key: coverage_report.closed_coverage.get(key, 0)
            for key in CLOSED_COVERAGE_KEYS
        }
    )

    candidate_ids = result.get("candidate_ids")
    if not isinstance(candidate_ids, list) or any(
        not isinstance(item, str) for item in candidate_ids
    ):
        raise ValueError("candidate inventory is invalid")
    candidate_count = len(candidate_ids)

    outcomes = result.get("outcomes")
    if not isinstance(outcomes, dict) or set(outcomes) - _OUTCOME_KEYS:
        raise ValueError("outcomes are invalid")
    safe_outcomes: dict[str, int] = {}
    for key, count in outcomes.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("outcome count is invalid")
        safe_outcomes[str(key)] = count
    if sum(safe_outcomes.values()) != candidate_count:
        raise ValueError("candidate outcome counts do not reconcile")
    if candidate_count < 1:
        raise ValueError("no candidates were imported")
    if safe_outcomes.get(ObservationDisposition.STAGED.value, 0) != 0:
        raise ValueError("staged candidates remain")
    if reconciled["counts"]["recognized"] < candidate_count:
        raise ValueError("coverage undercounts imported candidates")
    if not coverage_report.complete or reconciled["truthful_success"] is not True:
        raise ValueError("coverage is incomplete")

    return {
        "schema_version": 1,
        "status": "complete",
        "operation_status": "complete",
        "content_free": True,
        "provider": provider,
        "parser_identity": parser_identity,
        "export_format": export_format,
        "aggregate_parser_version": PARSER_VERSION,
        "closed_coverage": reconciled["counts"],
        "coverage_total": reconciled["total"],
        "outcomes": safe_outcomes,
        "candidate_count": candidate_count,
        "coverage_complete": True,
        "loopback_bound": True,
    }


def run_packaged_provider_acceptance(
    *,
    report_path: Path,
    export_path: Path,
    provider: str,
    data_dir: Path | None = None,
) -> int:
    """Import one export through packaged Core and emit no personal content."""

    report = report_path.expanduser().resolve()
    if report.exists():
        return 1
    try:
        normalized = normalize_provider(provider).value
    except ValueError:
        _write_report(report, _failure("provider_invalid"))
        return 1
    if normalized not in _MANDATORY_PROVIDERS:
        _write_report(report, _failure("provider_not_mandatory"))
        return 1

    try:
        source = export_path.expanduser().resolve(strict=True)
        if not source.is_file() or source.stat().st_size < 1:
            raise OSError
    except OSError:
        _write_report(report, _failure("export_missing_or_empty"))
        return 1

    if data_dir is None:
        owned_data_dir = True
        data_root = _make_temp_data_dir()
    else:
        owned_data_dir = False
        data_root = data_dir.expanduser().resolve()
        try:
            data_root.mkdir(parents=True, exist_ok=True)
            if any(data_root.iterdir()):
                _write_report(report, _failure("data_dir_not_empty"))
                return 1
        except OSError:
            _write_report(report, _failure("data_dir_unavailable"))
            return 1

    payload: dict[str, Any]
    try:
        config = CoreConfig.in_directory(data_root, require_auth=False)
        if config.host != "127.0.0.1":
            raise InvalidStateError("non-loopback host")
        try:
            with CoreService(config) as core:
                operation = core.import_operations.import_path_via_operation(
                    source,
                    filename=_safe_display_name(normalized, source),
                    source_service=normalized,
                    provider=normalized,
                )
        except (InvalidStateError, OSError, TypeError, UnicodeError, ValueError):
            # Production operation/import refused or failed before a complete result.
            payload = _failure("import_operation_failed")
        except Exception:
            # Process boundary: never persist exception text or dynamic type names.
            payload = _failure("import_failed")
        else:
            if operation.get("status") != "complete":
                # Operation reached a terminal or non-complete status without a usable result.
                payload = _failure("import_operation_incomplete")
            else:
                try:
                    raw_result = operation.get("result")
                    if not isinstance(raw_result, dict):
                        raise ValueError("missing import result")
                    payload = _successful_payload(raw_result, normalized)
                except (InvalidStateError, OSError, TypeError, UnicodeError, ValueError):
                    # Content-free acceptance reconciliation refused the terminal result.
                    payload = _failure("import_acceptance_reconcile_failed")
                except Exception:
                    payload = _failure("import_failed")
    except (InvalidStateError, OSError, TypeError, UnicodeError, ValueError):
        # Config/host/setup failure before the operation stage.
        payload = _failure("import_operation_failed")
    except Exception:
        payload = _failure("import_failed")

    cleanup_ok = True
    if owned_data_dir:
        try:
            shutil.rmtree(data_root)
        except OSError:
            cleanup_ok = False
    if not cleanup_ok:
        payload = _failure("data_dir_cleanup_failed")
    if not _write_report(report, payload):
        return 1
    return 0 if payload["status"] == "complete" else 1
