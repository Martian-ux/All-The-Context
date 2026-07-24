from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INTAKE_ROOT = REPOSITORY_ROOT / "research" / "competitor-intake"
MANIFEST_PATH = INTAKE_ROOT / "memory-systems-intake.v1.json"
VENDOR_CACHE = REPOSITORY_ROOT / "research" / "vendor-cache"

EXPECTED_CANDIDATES = {
    "a-mem",
    "agemem",
    "graphiti-zep",
    "hindsight",
    "hipporag2",
    "langmem-langgraph",
    "letta-memgpt",
    "mem0",
    "memos",
    "mirix",
    "reasoningbank",
}
DISPOSITIONS = {"adopt", "adapt", "observe", "reject"}


def _manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_memory_system_intake_manifest_has_complete_pinned_provenance() -> None:
    manifest = _manifest()

    assert manifest["schema_version"] == 1
    assert manifest["repository_baseline"]
    assert manifest["policy"] == {
        "official_sources_only": True,
        "license_confirmed_before_source_inspection": True,
        "third_party_code_executed": False,
        "third_party_dependencies_installed": False,
        "third_party_code_imported": False,
        "third_party_implementation_copied": False,
        "repositories_cloned": False,
        "clone_policy": (
            "Clone only after canonical origin and reuse-compatible license are confirmed; "
            "pin a commit; keep the clone in research/vendor-cache; never package or execute it."
        ),
        "common_clone_decision": "not_cloned",
        "common_clone_reason": (
            "Official GitHub metadata plus pinned license, README, and dependency files and "
            "canonical papers were sufficient; cloning added supply-chain and packaging risk "
            "without adding decision evidence."
        ),
    }

    candidates = manifest["candidates"]
    assert isinstance(candidates, list)
    assert {candidate["id"] for candidate in candidates} == EXPECTED_CANDIDATES
    assert sorted(candidate["rank"] for candidate in candidates) == list(
        range(1, len(candidates) + 1)
    )

    for candidate in candidates:
        assert candidate["disposition"] in DISPOSITIONS
        assert candidate["canonical_publications"] or candidate["official_documentation"]
        assert candidate["architecture_to_borrow"]
        assert candidate["integration_surface"]
        assert candidate["runtime_dependency_burden"]["rating"]
        assert candidate["runtime_dependency_burden"]["details"]
        assert candidate["license_caveats"]
        assert candidate["safety_risks"]
        assert candidate["decision_rationale"]

        repositories = candidate["official_repositories"]
        if not repositories:
            assert candidate["repository_gap"]
            continue

        for repository in repositories:
            revision = repository["pinned_revision"]
            assert len(revision) == 40
            assert all(character in "0123456789abcdef" for character in revision)
            assert repository["url"].startswith("https://github.com/")
            assert repository["clone_url"].endswith(".git")
            assert repository["commit_url"].endswith(revision)
            assert revision in repository["license_url"]
            assert repository["license_spdx"] in {"Apache-2.0", "MIT"}
            assert repository["archived"] is False


def test_vendor_cache_is_ignored_and_cannot_enter_python_package() -> None:
    ignore_rules = (VENDOR_CACHE / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ignore_rules == ["*", "!.gitignore", "!README.md"]
    assert {path.name for path in VENDOR_CACHE.iterdir()} == {".gitignore", "README.md"}

    root_ignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/research/vendor-cache/*" in root_ignore
    assert "!/research/vendor-cache/.gitignore" in root_ignore
    assert "!/research/vendor-cache/README.md" in root_ignore

    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    setuptools = pyproject["tool"]["setuptools"]
    assert setuptools["package-dir"] == {"": "packages/allthecontext/src"}
    assert "research" not in json.dumps(setuptools)
