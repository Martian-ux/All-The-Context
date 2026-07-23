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

IMAGE = f"ghcr.io/martian-ux/all-the-context-edge@sha256:{'1' * 64}"
REPOSITORY_URL = "https://github.com/Martian-ux/All-The-Context"
EXPERIMENTAL_TEMPLATE = """services:
  - type: web
    name: all-the-context-edge
    runtime: image
    plan: starter
    image:
      url: __ATC_EDGE_IMAGE_REFERENCE__
    healthCheckPath: /healthz
    autoDeploy: false
    numInstances: 1
    envVars:
      - key: ATC_EDGE_BUNDLE
        sync: false
      - key: ATC_RELAY_HOST
        value: 0.0.0.0
      - key: ATC_RELAY_DATABASE
        value: /var/lib/allthecontext/edge.sqlite3
    disk:
      name: all-the-context-edge-data
      mountPath: /var/lib/allthecontext
      sizeGB: 1
"""


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=repository, text=True).strip()


def _activation_repository(
    tmp_path: Path,
    *,
    mutation: str | None = None,
) -> tuple[Path, Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "acceptance@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "ATC acceptance"], cwd=repository, check=True)
    template = EXPERIMENTAL_TEMPLATE
    template_path = repository / "deploy" / "edge" / "render.template.yaml"
    template_path.parent.mkdir(parents=True)
    template_path.write_text(template, encoding="utf-8")
    subprocess.run(["git", "add", "deploy/edge/render.template.yaml"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Add trusted Edge template"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    source_commit = _git(repository, "rev-parse", "HEAD")
    blueprint = render_blueprint(template, IMAGE)
    if mutation == "command":
        blueprint = blueprint.replace(
            "    plan: starter\n",
            "    plan: starter\n    dockerCommand: python unexpected.py\n",
        )
    elif mutation == "service":
        blueprint += (
            "  - type: web\n"
            "    name: unexpected-service\n"
            "    runtime: image\n"
            f"    image:\n      url: {IMAGE}\n"
        )
    elif mutation == "environment":
        blueprint = blueprint.replace(
            "      - key: ATC_RELAY_HOST\n        value: 0.0.0.0\n",
            "      - key: ATC_RELAY_HOST\n        value: unexpected.example.invalid\n",
        )
    elif mutation is not None:  # pragma: no cover - defensive test helper
        raise AssertionError(f"unknown mutation: {mutation}")
    (repository / "render.yaml").write_text(blueprint, encoding="utf-8")
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
        json.dumps(edge_image_metadata(IMAGE, source_commit), indent=2, sort_keys=True) + "\n",
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


@pytest.mark.parametrize("mutation", ["command", "service", "environment"])
def test_activation_rejects_any_blueprint_behavior_not_in_source_template(
    tmp_path: Path,
    mutation: str,
) -> None:
    repository, metadata, commit = _activation_repository(tmp_path, mutation=mutation)
    defaults = tmp_path / "must-not-exist.py"

    def remote_must_not_be_queried(_repository: Path, _url: str, _branch: str) -> str:
        raise AssertionError("remote proof must follow local template verification")

    with pytest.raises(RuntimeError, match="trusted template"):
        activate_edge_deployment(
            repository=repository,
            metadata_path=metadata,
            blueprint_commit=commit,
            repository_url=REPOSITORY_URL,
            defaults_output=defaults,
            remote_resolver=remote_must_not_be_queried,
        )
    assert not defaults.exists()
