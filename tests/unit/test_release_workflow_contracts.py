"""Contract tests for release workflows, locked install, and publication allowlists."""

from __future__ import annotations

import re
from pathlib import Path

from allthecontext.acceptance_receipt import REQUIRED_PUBLICATION_GATES
from allthecontext.release_candidate import (
    ACCEPTANCE_RECEIPT_BUNDLE_FILE_NAME,
    COMPONENT_INVENTORY_FILE_NAME,
    DECISION_ASSET_NAMES,
    MATRIX_EVIDENCE_FILE_NAME,
    NOTICES_FILE_NAME,
    PUBLICATION_GATE_RECORD_FILE_NAME,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_release_candidate_validate_job_grants_actions_read() -> None:
    text = _read(WORKFLOWS / "release-candidate.yml")
    # Job-level permissions for the hosted-matrix Actions API query.
    assert re.search(
        r"validate:\s*\n(?:.*\n)*?\s+permissions:\s*\n(?:.*\n)*?\s+actions:\s*read",
        text,
    )
    assert "exact_source_gate.py hosted-matrix" in text
    assert "GITHUB_TOKEN: ${{ github.token }}" in text
    # Hosted-matrix step lives under the validate job that has actions:read.
    validate_block = text.split("native:")[0]
    assert "actions: read" in validate_block
    assert "hosted-matrix" in validate_block


def test_ci_declares_matrix_security_and_parity_job_names() -> None:
    from allthecontext.exact_source_gate import (
        REQUIRED_CI_JOBS,
        REQUIRED_CI_MATRIX_JOBS,
        REQUIRED_SECURITY_PARITY_JOBS,
    )

    text = _read(WORKFLOWS / "ci.yml")
    assert "name: Python 3.12 - ${{ matrix.os }}" in text or "Python 3.12 -" in text
    assert "Repository security gates" in text
    assert "Dashboard production asset parity" in text
    assert "Desktop artifact -" in text
    assert set(REQUIRED_SECURITY_PARITY_JOBS).issubset(set(REQUIRED_CI_JOBS))
    assert set(REQUIRED_CI_MATRIX_JOBS).issubset(set(REQUIRED_CI_JOBS))
    for name in REQUIRED_SECURITY_PARITY_JOBS:
        assert name in text
    # Hosted CI may build/test but must not deploy an Edge/runtime service.
    assert "deploy-pages" not in text
    assert "ghcr.io" not in text.casefold()
    assert "docker push" not in text.casefold()
    for forbidden in (
        "azure/webapps-deploy",
        "aws-actions",
        "google-github-actions/deploy",
        "prepare_edge_distribution",
        "activate_edge_deployment",
        "smoke_edge_container",
        "verify_edge_image",
    ):
        assert forbidden not in text


def test_workflows_pin_uv_and_do_not_bootstrap_unversioned_tools() -> None:
    install_script = _read(ROOT / "scripts" / "install_locked_python.py")
    audit_script = _read(ROOT / "scripts" / "dependency_audit.py")
    assert 'PINNED_UV_VERSION = "0.11.32"' in install_script
    assert "pip install" not in install_script or "--require-hashes" in install_script
    assert 'f"uv=={PINNED_UV_VERSION}"' not in install_script
    assert 'pip", "install", "--upgrade"' not in install_script
    assert "pinned uv==" in install_script
    assert "--no-hashes" not in install_script
    assert "--require-hashes" in install_script
    assert "--no-deps" in install_script
    assert "--no-build-isolation" in install_script
    assert 'BUILD_BACKEND_PACKAGES = ("packaging", "setuptools", "wheel")' in install_script
    assert "missing hashed build-environment packages" in install_script
    assert "pip-audit>=" not in audit_script
    assert "pip install" not in audit_script
    assert "importlib.metadata.version" in audit_script
    assert "--disable-pip" in audit_script
    assert "uv export" in audit_script or '"export"' in audit_script
    assert "pip-audit==2.10.1" in _read(ROOT / "pyproject.toml")
    pyproject = _read(ROOT / "pyproject.toml")
    assert "setuptools>=75" in pyproject
    assert '"wheel"' in pyproject

    for name in (
        "ci.yml",
        "release-candidate.yml",
        "publish-beta-release.yml",
    ):
        text = _read(WORKFLOWS / name)
        assert "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b" in text
        assert 'version: "0.11.32"' in text
        assert "pip install uv" not in text
        assert "pip-audit>=" not in text


def test_required_publication_gates_appear_in_templates_not_r05() -> None:
    import json

    template_path = ROOT / "release" / "acceptance-receipt-bundle.template.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    gate_ids = {item["gate_id"] for item in template["receipts"]}
    assert gate_ids == REQUIRED_PUBLICATION_GATES
    assert "BETA-R05" not in gate_ids
    assert all(item["status"] == "not_run" for item in template["receipts"])
    assert all(item["status"] != "pass" for item in template["receipts"])
    assert template["maintainer_decision"]["independent_human_review_claimed"] is False


def test_publish_workflow_persists_decision_artifacts_before_final_recheck() -> None:
    text = _read(WORKFLOWS / "publish-beta-release.yml")
    assert "--asset-stage signed" in text
    assert "--asset-stage promotion" in text
    assert ACCEPTANCE_RECEIPT_BUNDLE_FILE_NAME in text
    assert PUBLICATION_GATE_RECORD_FILE_NAME in text
    assert "https://uploads.github.com/repos/" in text
    assert "--hostname uploads.github.com" not in text
    assert "decision_attest" in text or "Attest acceptance" in text
    # Upload happens before the pre-publish recheck.
    upload_at = text.index("https://uploads.github.com/repos/")
    recheck_at = text.index("Recheck the exact promotion asset set")
    assert upload_at < recheck_at


def test_unpublished_release_workflows_use_unique_numeric_release_identity() -> None:
    candidate = _read(WORKFLOWS / "release-candidate.yml")
    assert "gh api --paginate --slurp" in candidate
    assert "resolve-release" in candidate
    assert 'repos/$GITHUB_REPOSITORY/releases/$release_id' in candidate
    assert 'gh release view "$TAG"' not in candidate
    assert "git/ref/tags/$TAG" not in candidate

    publish = _read(WORKFLOWS / "publish-beta-release.yml")
    prepublication, postpublication = publish.split(
        "- name: Require immutable published state and GitHub release attestation",
        maxsplit=1,
    )
    for tag_command in (
        "gh release view",
        "gh release download",
        "gh release upload",
        "gh release edit",
        "git/ref/tags/",
    ):
        assert tag_command not in prepublication
    assert "resolve-release" in prepublication
    assert "list-release-assets" in prepublication
    assert "releases/assets/$asset_id" in prepublication
    assert "https://uploads.github.com/repos/" in prepublication
    assert "--hostname uploads.github.com" not in prepublication
    assert '"repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID"' in prepublication
    assert "--method PATCH" in prepublication
    assert "gh release view" in postpublication
    assert "git/ref/tags/" in postpublication
    assert "gh release verify" in postpublication


def test_release_candidate_binds_source_evidence_into_inventory() -> None:
    text = _read(WORKFLOWS / "release-candidate.yml")
    assert "--source-evidence-dir dist/source-evidence" in text
    assert MATRIX_EVIDENCE_FILE_NAME in text
    assert COMPONENT_INVENTORY_FILE_NAME in text
    assert NOTICES_FILE_NAME in text
    module = _read(
        ROOT / "packages" / "allthecontext" / "src" / "allthecontext" / "release_candidate.py"
    )
    assert "source_evidence" in module
    assert "DECISION_ASSET_NAMES" in module
    for name in DECISION_ASSET_NAMES:
        assert name in module


def test_component_inventory_scope_and_no_invented_license_text() -> None:
    from allthecontext.component_inventory import build_component_inventory

    inventory = build_component_inventory(
        ROOT,
        source_commit="c" * 40,
        version="0.1.0-beta.1",
    )
    scopes = {item["scope"] for item in inventory["components"]}
    assert scopes <= {"runtime", "build", "dev"}
    assert "runtime" in scopes
    assert "dev" in scopes
    python = [item for item in inventory["components"] if item["ecosystem"] == "python"]
    project = next(item for item in python if item["name"] == "all-the-context")
    assert project["license"] == "MIT"
    third_party = [item for item in python if item["name"] != "all-the-context"]
    assert all(item["license"] == "NOASSERTION" for item in third_party)
    assert any(item["name"] == "pip-audit" and item["scope"] == "dev" for item in python)


def test_native_workflows_pin_packaged_recovery_and_locked_python() -> None:
    """Integrated candidate/CI native matrix must exercise recovery from built bytes."""

    for name in ("ci.yml", "release-candidate.yml"):
        text = _read(WORKFLOWS / name)
        assert "scripts/install_locked_python.py" in text
        assert "scripts/build_desktop.py" in text
        assert "scripts/smoke_desktop_artifact.py" in text
        assert "scripts/smoke_packaged_recovery.py" in text
        assert "scripts/smoke_packaged_first_run.py" in text
        assert "scripts/package_desktop.py" in text


def test_integrated_package_data_and_recovery_helpers_are_pinned() -> None:
    """Stale asset/helper/migration lists must not silently drop integrated surfaces."""

    pyproject = _read(ROOT / "pyproject.toml")
    assert "migrations/**/*.sql" in pyproject or "migrations/**/*.sql" in pyproject
    assert "web/**/*" in pyproject
    assert (ROOT / "scripts" / "recovery_entry.py").is_file()
    assert (
        ROOT
        / "packages"
        / "allthecontext"
        / "src"
        / "allthecontext"
        / "migrations"
        / "core"
        / "009_import_operations.sql"
    ).is_file()
    web = ROOT / "packages" / "allthecontext" / "src" / "allthecontext" / "web"
    assert (web / "index.html").is_file()
    dashboard_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in web.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".js", ".html"}
    )
    assert "import-operations" in dashboard_text or "importOperations" in dashboard_text

    build_desktop = _read(ROOT / "scripts" / "build_desktop.py")
    assert "AllTheContextRecovery" in build_desktop
    assert "all-the-context-recovery" in build_desktop
    assert "recovery_entry.py" in build_desktop
    assert "--collect-data" in build_desktop

    diagnose = _read(ROOT / "scripts" / "diagnose_python_packages.py")
    assert "009_import_operations.sql" in diagnose
    assert "web/index.html" in diagnose

    smoke_recovery = _read(ROOT / "scripts" / "smoke_packaged_recovery.py")
    assert "frozen-windowed-desktop-fallback" not in smoke_recovery
    assert 'return [sys.executable, "-m", "allthecontext.desktop"]' not in smoke_recovery
    assert "AllTheContextRecovery.exe" in smoke_recovery
    assert "all-the-context-recovery" in smoke_recovery
    assert "SystemExit" in smoke_recovery
    assert "recovery-helper-dist" in smoke_recovery
    assert "beta_d03_acceptance" in smoke_recovery

    package_desktop = _read(ROOT / "scripts" / "package_desktop.py")
    assert "recovery_surface" in package_desktop
    assert "embedded-console-helper" in package_desktop
    assert "console-main-binary" in package_desktop

    first_run = _read(ROOT / "scripts" / "smoke_packaged_first_run.py")
    assert "AllTheContextRecovery.exe" in first_run
    assert "rollback_recovery" in first_run
    # Isolated first-run smoke must opt into the development credential file
    # while forcing a null OS keyring, assert the fallback store, and never
    # claim real OS credential acceptance (that is a separate gate).
    assert "DEVELOPMENT_FALLBACK_ENV" in first_run
    assert "FALLBACK_CREDENTIAL_STORAGE" in first_run
    assert "keyring.backends.null.Keyring" in first_run
    assert "credential_storage" in first_run
    assert "os_credential_acceptance" in first_run
    assert "not_this_smoke" in first_run
    assert "build_failure_diagnostic_summary" in first_run
    assert "remove_work_tree" in first_run
    assert "packaged-first-run-diagnostics" in first_run
    assert "retain_work_on_failure" not in first_run
    assert 'print(f"{label} stdout' not in first_run
    assert 'print(f"{label} stderr' not in first_run
    assert "packaged-credential-acceptance" in first_run
    # Never silently treat first-run smoke as production OS-store proof.
    assert "ATC_ENABLE_INSECURE_DEVELOPMENT_CREDENTIAL_FILE" in first_run or (
        "DEVELOPMENT_FALLBACK_ENV" in first_run
    )

    artifact_smoke = _read(ROOT / "scripts" / "smoke_desktop_artifact.py")
    assert (
        "009_import_operations" in artifact_smoke or "import_operations_migration" in artifact_smoke
    )
    assert "dashboard_import_operations" in artifact_smoke
    assert "--packaged-credential-acceptance" in artifact_smoke
