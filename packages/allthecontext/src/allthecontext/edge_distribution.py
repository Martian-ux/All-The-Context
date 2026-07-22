"""Fail-closed metadata and deployment configuration for the hosted Edge."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

from . import edge_deployment_defaults

BLUEPRINT_IMAGE_PLACEHOLDER = "__ATC_EDGE_IMAGE_REFERENCE__"
EDGE_IMAGE_NAME = "all-the-context-edge"
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_IMAGE_PATTERN = re.compile(
    r"ghcr\.io/(?P<owner>[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?)/"
    r"(?P<name>[a-z0-9](?:[a-z0-9._/-]{0,253}[a-z0-9])?)@"
    r"(?P<digest>sha256:[0-9a-f]{64})"
)
_DEPLOY_BRANCH_PATTERN = re.compile(r"edge-deploy-[0-9a-f]{64}")


class EdgeDistributionError(ValueError):
    """A distribution value would weaken the immutable Edge handoff."""


def validate_source_commit(value: str) -> str:
    """Require one nonzero, full lowercase Git commit SHA."""

    normalized = value.strip().lower()
    if _COMMIT_PATTERN.fullmatch(normalized) is None or normalized == "0" * 40:
        raise EdgeDistributionError("source commit must be a full nonzero 40-character SHA")
    return normalized


def validate_image_reference(value: str) -> str:
    """Require a lowercase GHCR reference pinned to an exact nonzero digest."""

    normalized = value.strip()
    match = _IMAGE_PATTERN.fullmatch(normalized)
    if match is None or normalized != normalized.lower():
        raise EdgeDistributionError(
            "Edge image must be a lowercase digest-addressed ghcr.io reference"
        )
    if match.group("name") != EDGE_IMAGE_NAME:
        raise EdgeDistributionError(f"Edge image package must be named {EDGE_IMAGE_NAME}")
    if match.group("digest") == f"sha256:{'0' * 64}":
        raise EdgeDistributionError("Edge image digest cannot be the zero placeholder")
    return normalized


def image_digest(value: str) -> str:
    reference = validate_image_reference(value)
    return reference.rsplit("@", maxsplit=1)[1]


def validate_render_deploy_url(value: str) -> str:
    """Canonicalize a Render link pinned to the reviewed Blueprint commit."""

    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise EdgeDistributionError("Render deploy URL contains an invalid port") from exc
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.hostname.lower() != "render.com"
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/deploy"
        or parsed.fragment
    ):
        raise EdgeDistributionError("deploy URL must be https://render.com/deploy")
    try:
        query = parse_qs(parsed.query, strict_parsing=True)
    except ValueError as exc:
        raise EdgeDistributionError("Render deploy URL query is invalid") from exc
    if set(query) != {"repo"} or len(query["repo"]) != 1:
        raise EdgeDistributionError("Render deploy URL must contain exactly one repo parameter")
    repository = urlsplit(query["repo"][0])
    try:
        repository_port = repository.port
    except ValueError as exc:
        raise EdgeDistributionError("Render repository URL contains an invalid port") from exc
    path_parts = [part for part in repository.path.split("/") if part]
    if (
        repository.scheme.lower() != "https"
        or repository.hostname is None
        or repository.hostname.lower() != "github.com"
        or repository.username is not None
        or repository.password is not None
        or repository_port not in {None, 443}
        or repository.query
        or repository.fragment
        or len(path_parts) != 4
        or path_parts[2] != "tree"
        or _DEPLOY_BRANCH_PATTERN.fullmatch(path_parts[3]) is None
    ):
        raise EdgeDistributionError(
            "Render repository must use the versioned Edge deployment branch"
        )
    repository_url = urlunsplit(
        (
            "https",
            repository.hostname.lower(),
            repository.path.rstrip("/"),
            "",
            "",
        )
    )
    return f"https://render.com/deploy?repo={quote(repository_url, safe='')}"


def deployment_branch(image_reference: str) -> str:
    """Derive the one-use Render branch name from the reviewed image digest."""

    digest = image_digest(image_reference).removeprefix("sha256:")
    return f"edge-deploy-{digest}"


def render_deploy_url(repository_url: str, deploy_branch: str) -> str:
    """Build the Render link for a one-use branch verified by the operator."""

    if _DEPLOY_BRANCH_PATTERN.fullmatch(deploy_branch) is None:
        raise EdgeDistributionError("Edge deployment branch name is invalid")
    normalized_repository = normalize_github_repository_url(repository_url)
    pinned_repository = f"{normalized_repository}/tree/{deploy_branch}"
    return validate_render_deploy_url(
        f"https://render.com/deploy?repo={quote(pinned_repository, safe='')}"
    )


def normalize_github_repository_url(repository_url: str) -> str:
    """Return the exact public repository origin used by both proof and button."""

    repository = urlsplit(repository_url.strip())
    try:
        port = repository.port
    except ValueError as exc:
        raise EdgeDistributionError("GitHub repository URL contains an invalid port") from exc
    parts = [part for part in repository.path.split("/") if part]
    if (
        repository.scheme.lower() != "https"
        or repository.hostname is None
        or repository.hostname.lower() != "github.com"
        or port not in {None, 443}
        or repository.username is not None
        or repository.password is not None
        or repository.query
        or repository.fragment
        or len(parts) != 2
    ):
        raise EdgeDistributionError("repository must be an HTTPS GitHub owner/repository URL")
    return f"https://github.com/{parts[0]}/{parts[1]}"


@dataclass(frozen=True, slots=True)
class EdgeDeploymentConfig:
    deploy_url: str | None
    deploy_branch: str | None
    image_reference: str | None
    source_commit: str | None
    blueprint_commit: str | None
    source: str
    error: str | None = None

    @property
    def enabled(self) -> bool:
        return (
            self.error is None
            and self.deploy_url is not None
            and self.deploy_branch is not None
            and self.image_reference is not None
            and self.source_commit is not None
            and self.blueprint_commit is not None
        )


def deployment_config(
    environ: Mapping[str, str] | None = None,
) -> EdgeDeploymentConfig:
    """Load an all-or-nothing reviewed deployment handoff without exposing bad values."""

    values = os.environ if environ is None else environ
    environment_values = (
        values.get("ATC_EDGE_DEPLOY_URL", "").strip(),
        values.get("ATC_EDGE_DEPLOY_BRANCH", "").strip(),
        values.get("ATC_EDGE_IMAGE_REFERENCE", "").strip(),
        values.get("ATC_EDGE_SOURCE_COMMIT", "").strip(),
        values.get("ATC_EDGE_BLUEPRINT_COMMIT", "").strip(),
    )
    if any(environment_values):
        source = "environment"
        raw_url, raw_branch, raw_image, raw_commit, raw_blueprint_commit = environment_values
    else:
        source = "packaged"
        raw_url = edge_deployment_defaults.EDGE_DEPLOY_URL or ""
        raw_branch = edge_deployment_defaults.EDGE_DEPLOY_BRANCH or ""
        raw_image = edge_deployment_defaults.EDGE_IMAGE_REFERENCE or ""
        raw_commit = edge_deployment_defaults.EDGE_SOURCE_COMMIT or ""
        raw_blueprint_commit = edge_deployment_defaults.EDGE_BLUEPRINT_COMMIT or ""
    if not any((raw_url, raw_branch, raw_image, raw_commit, raw_blueprint_commit)):
        return EdgeDeploymentConfig(None, None, None, None, None, source)
    if not all((raw_url, raw_branch, raw_image, raw_commit, raw_blueprint_commit)):
        return EdgeDeploymentConfig(
            None,
            None,
            None,
            None,
            None,
            source,
            "The Edge deployment handoff is incomplete and was disabled.",
        )
    try:
        reference = validate_image_reference(raw_image)
        url = validate_render_deploy_url(raw_url)
        if raw_branch != deployment_branch(reference) or not url.endswith(
            quote(f"/tree/{raw_branch}", safe="")
        ):
            raise EdgeDistributionError("deployment branch is not bound to the image digest")
        return EdgeDeploymentConfig(
            url,
            raw_branch,
            reference,
            validate_source_commit(raw_commit),
            validate_source_commit(raw_blueprint_commit),
            source,
        )
    except EdgeDistributionError:
        return EdgeDeploymentConfig(
            None,
            None,
            None,
            None,
            None,
            source,
            "The Edge deployment handoff failed immutable-reference validation.",
        )


def edge_image_metadata(image_reference: str, source_commit: str) -> dict[str, object]:
    """Return deterministic, non-secret OCI handoff metadata."""

    reference = validate_image_reference(image_reference)
    commit = validate_source_commit(source_commit)
    return {
        "deployment_branch": deployment_branch(reference),
        "image": {
            "digest": image_digest(reference),
            "reference": reference,
            "registry": "ghcr.io",
        },
        "platforms": ["linux/amd64"],
        "provenance": {
            "buildkit": "mode=max",
            "github_attestation": True,
            "sbom": True,
        },
        "schema_version": 1,
        "source_commit": commit,
    }


def parse_edge_image_metadata(value: object) -> tuple[str, str]:
    """Validate the complete workflow handoff without accepting extra fields."""

    if not isinstance(value, Mapping) or set(value) != {
        "deployment_branch",
        "image",
        "platforms",
        "provenance",
        "schema_version",
        "source_commit",
    }:
        raise EdgeDistributionError("Edge image metadata fields are invalid")
    image = value.get("image")
    if not isinstance(image, Mapping) or set(image) != {"digest", "reference", "registry"}:
        raise EdgeDistributionError("Edge image metadata identity is invalid")
    reference = validate_image_reference(str(image.get("reference", "")))
    commit = validate_source_commit(str(value.get("source_commit", "")))
    if dict(value) != edge_image_metadata(reference, commit):
        raise EdgeDistributionError("Edge image metadata does not match its exact reference")
    return reference, commit


def render_blueprint(template: str, image_reference: str) -> str:
    """Replace the sole image gate with one exact digest-addressed reference."""

    reference = validate_image_reference(image_reference)
    if template.count(BLUEPRINT_IMAGE_PLACEHOLDER) != 1:
        raise EdgeDistributionError("Render blueprint must contain exactly one image placeholder")
    rendered = template.replace(BLUEPRINT_IMAGE_PLACEHOLDER, reference)
    validate_pinned_blueprint(rendered, reference)
    return rendered


def validate_pinned_blueprint(blueprint: str, image_reference: str) -> None:
    """Validate the exact committed Blueprint consumed by the deploy link."""

    reference = validate_image_reference(image_reference)
    if BLUEPRINT_IMAGE_PLACEHOLDER in blueprint:
        raise EdgeDistributionError("enabled Render blueprint still contains the image placeholder")
    if "runtime: docker" in blueprint or "dockerfilePath:" in blueprint:
        raise EdgeDistributionError("Render must pull the reviewed image instead of rebuilding")
    if blueprint.count(f"url: {reference}") != 1:
        raise EdgeDistributionError("Render blueprint did not preserve the exact image reference")
    image_urls = re.findall(r"^\s+url:\s+(\S+)\s*$", blueprint, flags=re.MULTILINE)
    if image_urls != [reference]:
        raise EdgeDistributionError("Render blueprint contains an unexpected image reference")


def render_packaged_defaults(
    *,
    deploy_url: str,
    deploy_branch: str,
    image_reference: str,
    source_commit: str,
    blueprint_commit: str,
) -> str:
    """Render the small Python module embedded in a reviewed Core package."""

    url = validate_render_deploy_url(deploy_url)
    reference = validate_image_reference(image_reference)
    commit = validate_source_commit(source_commit)
    blueprint = validate_source_commit(blueprint_commit)
    if deploy_branch != deployment_branch(reference) or not url.endswith(
        quote(f"/tree/{deploy_branch}", safe="")
    ):
        raise EdgeDistributionError("deployment branch is not bound to the image digest")
    return (
        '"""Generated reviewed hosted Edge defaults. Do not edit by hand."""\n\n'
        "from __future__ import annotations\n\n"
        f"EDGE_DEPLOY_URL: str | None = {url!r}\n"
        f"EDGE_DEPLOY_BRANCH: str | None = {deploy_branch!r}\n"
        f"EDGE_IMAGE_REFERENCE: str | None = {reference!r}\n"
        f"EDGE_SOURCE_COMMIT: str | None = {commit!r}\n"
        f"EDGE_BLUEPRINT_COMMIT: str | None = {blueprint!r}\n"
    )
