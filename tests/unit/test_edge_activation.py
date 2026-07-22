from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from allthecontext.edge_activation import activate_edge_deployment
from allthecontext.edge_distribution import (
    deployment_branch,
    edge_image_metadata,
    render_blueprint,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
IMAGE = f"ghcr.io/martian-ux/all-the-context-edge@sha256:{'1' * 64}"
SOURCE_COMMIT = "a" * 40
REPOSITORY_URL = "https://github.com/Martian-ux/All-The-Context"


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=repository, text=True).strip()


def _activation_repository(tmp_path: Path) -> tuple[Path, Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "acceptance@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "ATC acceptance"], cwd=repository, check=True)
    template = (REPOSITORY_ROOT / "deploy" / "edge" / "render.template.yaml").read_text(
        encoding="utf-8"
    )
    (repository / "render.yaml").write_text(render_blueprint(template, IMAGE), encoding="utf-8")
    subprocess.run(["git", "add", "render.yaml"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Pin Edge blueprint"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    commit = _git(repository, "rev-parse", "HEAD")
    metadata = tmp_path / "edge-image.json"
    metadata.write_text(
        json.dumps(edge_image_metadata(IMAGE, SOURCE_COMMIT), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return repository, metadata, commit


def test_activation_binds_remote_proof_to_exact_deploy_repository(tmp_path: Path) -> None:
    repository, metadata, commit = _activation_repository(tmp_path)
    branch = deployment_branch(IMAGE)
    observed: list[tuple[str, str]] = []

    def resolve(_repository: Path, repository_url: str, requested_branch: str) -> str:
        observed.append((repository_url, requested_branch))
        return f"{commit}\trefs/heads/{requested_branch}"

    defaults = tmp_path / "edge_deployment_defaults.py"
    result = activate_edge_deployment(
        repository=repository,
        metadata_path=metadata,
        blueprint_commit=commit,
        repository_url=REPOSITORY_URL,
        defaults_output=defaults,
        remote_resolver=resolve,
    )

    assert observed == [(REPOSITORY_URL, branch)]
    assert result["repository_url"] == REPOSITORY_URL
    assert result["deploy_branch"] == branch
    assert f"%2Ftree%2F{branch}" in str(result["deploy_url"])
    generated = defaults.read_text(encoding="utf-8")
    assert repr(commit) in generated
    assert repr(IMAGE) in generated


def test_activation_rejects_branch_proof_from_a_different_repository(tmp_path: Path) -> None:
    repository, metadata, commit = _activation_repository(tmp_path)

    def mismatched_repository(_repository: Path, repository_url: str, requested_branch: str) -> str:
        assert repository_url == REPOSITORY_URL
        # A different repository's similarly named branch resolves elsewhere.
        return f"{'f' * 40}\trefs/heads/{requested_branch}"

    defaults = tmp_path / "must-not-exist.py"
    with pytest.raises(RuntimeError, match="does not resolve"):
        activate_edge_deployment(
            repository=repository,
            metadata_path=metadata,
            blueprint_commit=commit,
            repository_url=REPOSITORY_URL,
            defaults_output=defaults,
            remote_resolver=mismatched_repository,
        )
    assert not defaults.exists()
