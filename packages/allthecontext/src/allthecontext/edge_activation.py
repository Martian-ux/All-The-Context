"""Repository-bound activation of the hosted Edge deployment button."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from .edge_distribution import (
    deployment_branch,
    normalize_github_repository_url,
    parse_edge_image_metadata,
    render_deploy_url,
    render_packaged_defaults,
    validate_pinned_blueprint,
    validate_source_commit,
)

RemoteResolver = Callable[[Path, str, str], str]


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments],
        cwd=repository,
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def _resolve_remote_branch(repository: Path, repository_url: str, branch: str) -> str:
    """Query the exact public URL that will be encoded into the Render button."""

    return _git(repository, "ls-remote", "--exit-code", "--heads", repository_url, branch)


def activate_edge_deployment(
    *,
    repository: Path,
    metadata_path: Path,
    blueprint_commit: str,
    repository_url: str,
    defaults_output: Path,
    remote_resolver: RemoteResolver = _resolve_remote_branch,
) -> dict[str, object]:
    """Verify local and public branch state, then replace only packaged defaults."""

    root = repository.expanduser().resolve()
    metadata = json.loads(metadata_path.expanduser().resolve().read_text(encoding="utf-8"))
    image_reference, source_commit = parse_edge_image_metadata(metadata)
    blueprint_sha = validate_source_commit(blueprint_commit)
    normalized_repository = normalize_github_repository_url(repository_url)
    head = _git(root, "rev-parse", "HEAD")
    if head != blueprint_sha:
        raise RuntimeError("activate from the exact commit that added the pinned Blueprint")
    branch = deployment_branch(image_reference)
    committed_blueprint = _git(root, "show", f"{blueprint_sha}:render.yaml") + "\n"
    validate_pinned_blueprint(committed_blueprint, image_reference)
    working_blueprint = (root / "render.yaml").read_text(encoding="utf-8")
    if working_blueprint != committed_blueprint:
        raise RuntimeError("working render.yaml differs from the committed Blueprint")
    remote_lines = remote_resolver(root, normalized_repository, branch).splitlines()
    expected_ref = f"refs/heads/{branch}"
    remote_matches = [
        line.split("\t", maxsplit=1)
        for line in remote_lines
        if "\t" in line and line.split("\t", maxsplit=1)[1] == expected_ref
    ]
    if remote_matches != [[blueprint_sha, expected_ref]]:
        raise RuntimeError("versioned deployment branch does not resolve to the Blueprint commit")
    deploy_url = render_deploy_url(normalized_repository, branch)
    defaults = render_packaged_defaults(
        deploy_url=deploy_url,
        deploy_branch=branch,
        image_reference=image_reference,
        source_commit=source_commit,
        blueprint_commit=blueprint_sha,
    )
    output = defaults_output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.name}.tmp")
    try:
        temporary.write_text(defaults, encoding="utf-8")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "blueprint_commit": blueprint_sha,
        "defaults_output": str(output),
        "deploy_branch": branch,
        "deploy_url": deploy_url,
        "image_reference": image_reference,
        "operator_review_required": True,
        "provider_deployment_performed": False,
        "repository_url": normalized_repository,
        "source_commit": source_commit,
    }
