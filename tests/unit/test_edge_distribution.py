from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import Request

import pytest
from allthecontext import edge_deployment_defaults
from allthecontext.edge_distribution import (
    BLUEPRINT_IMAGE_PLACEHOLDER,
    EdgeDistributionError,
    deployment_config,
    edge_image_metadata,
    render_blueprint,
    render_packaged_defaults,
    validate_image_reference,
    validate_pinned_blueprint,
    validate_render_deploy_url,
)
from allthecontext.edge_registry import EdgeRegistryError, verify_anonymous_ghcr_pull

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_COMMIT = "a" * 40
BLUEPRINT_COMMIT = "b" * 40
MANIFEST = b'{"manifests":[],"mediaType":"application/vnd.oci.image.index.v1+json"}'
DIGEST = f"sha256:{hashlib.sha256(MANIFEST).hexdigest()}"
IMAGE = f"ghcr.io/martian-ux/all-the-context-edge@{DIGEST}"
DEPLOY_BRANCH = f"edge-deploy-{DIGEST.removeprefix('sha256:')}"
DEPLOY_URL = (
    "https://render.com/deploy?"
    "repo=https%3A%2F%2Fgithub.com%2FMartian-ux%2FAll-The-Context%2Ftree%2F"
    f"{DEPLOY_BRANCH}"
)
SAMPLE_BRANCH = f"edge-deploy-{'1' * 64}"
SAMPLE_REPOSITORY = f"https://github.com/example/project/tree/{SAMPLE_BRANCH}"


class FakeResponse:
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self.payload if amount < 0 else self.payload[:amount]


def test_distribution_metadata_and_blueprint_are_deterministic_and_digest_pinned() -> None:
    template = (REPOSITORY_ROOT / "deploy" / "edge" / "render.template.yaml").read_text(
        encoding="utf-8"
    )

    metadata = edge_image_metadata(IMAGE, SOURCE_COMMIT)
    rendered = render_blueprint(template, IMAGE)
    defaults = render_packaged_defaults(
        deploy_url=DEPLOY_URL,
        deploy_branch=DEPLOY_BRANCH,
        image_reference=IMAGE,
        source_commit=SOURCE_COMMIT,
        blueprint_commit=BLUEPRINT_COMMIT,
    )

    assert metadata == edge_image_metadata(IMAGE, SOURCE_COMMIT)
    assert metadata["image"] == {
        "digest": DIGEST,
        "reference": IMAGE,
        "registry": "ghcr.io",
    }
    assert metadata["deployment_branch"] == DEPLOY_BRANCH
    assert json.loads(json.dumps(metadata, sort_keys=True))["source_commit"] == SOURCE_COMMIT
    assert BLUEPRINT_IMAGE_PLACEHOLDER not in rendered
    assert f"url: {IMAGE}" in rendered
    assert "runtime: image" in rendered
    assert "runtime: docker" not in rendered
    assert "dockerfilePath:" not in rendered
    assert repr(DEPLOY_URL) in defaults
    assert repr(IMAGE) in defaults
    assert repr(SOURCE_COMMIT) in defaults
    assert repr(BLUEPRINT_COMMIT) in defaults


def test_permanent_template_survives_first_activation_for_the_next_edge_image() -> None:
    template_path = REPOSITORY_ROOT / "deploy" / "edge" / "render.template.yaml"
    template = template_path.read_text(encoding="utf-8")
    first_root = render_blueprint(template, IMAGE)
    next_digest = f"sha256:{'2' * 64}"
    next_image = f"ghcr.io/martian-ux/all-the-context-edge@{next_digest}"

    assert BLUEPRINT_IMAGE_PLACEHOLDER not in first_root
    assert BLUEPRINT_IMAGE_PLACEHOLDER in template_path.read_text(encoding="utf-8")
    second_root = render_blueprint(template_path.read_text(encoding="utf-8"), next_image)
    assert f"url: {next_image}" in second_root


def test_committed_packaged_default_can_only_enable_an_exact_root_blueprint() -> None:
    config = deployment_config({})
    root_blueprint = (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8")
    permanent_template = (REPOSITORY_ROOT / "deploy" / "edge" / "render.template.yaml").read_text(
        encoding="utf-8"
    )

    assert permanent_template.count(BLUEPRINT_IMAGE_PLACEHOLDER) == 1
    if config.enabled:
        assert config.image_reference is not None
        validate_pinned_blueprint(root_blueprint, config.image_reference)
    else:
        assert config.deploy_url is None


@pytest.mark.parametrize(
    "reference",
    [
        "ghcr.io/martian-ux/all-the-context-edge:latest",
        f"ghcr.io/Martian-ux/all-the-context-edge@{DIGEST}",
        f"docker.io/martian-ux/all-the-context-edge@{DIGEST}",
        f"ghcr.io/martian-ux/wrong-name@{DIGEST}",
        f"ghcr.io/martian-ux/all-the-context-edge@sha256:{'0' * 64}",
    ],
)
def test_image_reference_rejects_mutable_or_wrong_distribution_targets(reference: str) -> None:
    with pytest.raises(EdgeDistributionError):
        validate_image_reference(reference)


@pytest.mark.parametrize(
    "url",
    [
        f"http://render.com/deploy?repo={SAMPLE_REPOSITORY}",
        f"https://evil.example/deploy?repo={SAMPLE_REPOSITORY}",
        f"https://user:secret@render.com/deploy?repo={SAMPLE_REPOSITORY}",
        f"https://render.com/deploy?repo={SAMPLE_REPOSITORY.replace('https:', 'http:')}",
        "https://render.com/deploy?repo=https://github.com/example/project/tree/main",
        f"https://render.com/deploy?repo={SAMPLE_REPOSITORY}&redirect=evil",
    ],
)
def test_render_deploy_url_rejects_unsafe_links(url: str) -> None:
    with pytest.raises(EdgeDistributionError):
        validate_render_deploy_url(url)


def test_runtime_handoff_is_all_or_nothing_and_packaged_default_starts_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert deployment_config({}).enabled is False

    partial = deployment_config({"ATC_EDGE_DEPLOY_URL": DEPLOY_URL})
    assert partial.enabled is False
    assert partial.deploy_url is None
    assert DEPLOY_URL not in str(partial.error)

    invalid = deployment_config(
        {
            "ATC_EDGE_DEPLOY_URL": DEPLOY_URL,
            "ATC_EDGE_DEPLOY_BRANCH": DEPLOY_BRANCH,
            "ATC_EDGE_IMAGE_REFERENCE": "private invalid value",
            "ATC_EDGE_SOURCE_COMMIT": SOURCE_COMMIT,
            "ATC_EDGE_BLUEPRINT_COMMIT": BLUEPRINT_COMMIT,
        }
    )
    assert invalid.enabled is False
    assert "private invalid value" not in str(invalid.error)

    valid = deployment_config(
        {
            "ATC_EDGE_DEPLOY_URL": DEPLOY_URL,
            "ATC_EDGE_DEPLOY_BRANCH": DEPLOY_BRANCH,
            "ATC_EDGE_IMAGE_REFERENCE": IMAGE,
            "ATC_EDGE_SOURCE_COMMIT": SOURCE_COMMIT,
            "ATC_EDGE_BLUEPRINT_COMMIT": BLUEPRINT_COMMIT,
        }
    )
    assert valid.enabled is True
    assert valid.deploy_url == DEPLOY_URL

    monkeypatch.setattr(edge_deployment_defaults, "EDGE_DEPLOY_URL", DEPLOY_URL)
    monkeypatch.setattr(edge_deployment_defaults, "EDGE_DEPLOY_BRANCH", DEPLOY_BRANCH)
    monkeypatch.setattr(edge_deployment_defaults, "EDGE_IMAGE_REFERENCE", IMAGE)
    monkeypatch.setattr(edge_deployment_defaults, "EDGE_SOURCE_COMMIT", SOURCE_COMMIT)
    monkeypatch.setattr(edge_deployment_defaults, "EDGE_BLUEPRINT_COMMIT", BLUEPRINT_COMMIT)
    packaged = deployment_config({})
    assert packaged.enabled is True
    assert packaged.source == "packaged"


def test_anonymous_registry_verifier_hashes_exact_manifest_without_basic_credentials() -> None:
    requests: list[Request] = []

    def opener(request: Request, _timeout: float) -> FakeResponse:
        requests.append(request)
        if len(requests) == 1:
            assert request.get_header("Authorization") is None
            return FakeResponse(json.dumps({"token": "t" * 32}).encode())
        assert request.get_header("Authorization") == f"Bearer {'t' * 32}"
        return FakeResponse(
            MANIFEST,
            {
                "Docker-Content-Digest": DIGEST,
                "Content-Type": "application/vnd.oci.image.index.v1+json",
            },
        )

    evidence = verify_anonymous_ghcr_pull(IMAGE, opener=opener)  # type: ignore[arg-type]

    assert evidence.manifest_digest == DIGEST
    assert evidence.manifest_bytes == len(MANIFEST)
    assert "t" * 32 not in json.dumps(evidence.mapping())
    assert len(requests) == 2


def test_anonymous_registry_verifier_rejects_manifest_digest_mismatch() -> None:
    count = 0

    def opener(_request: Request, _timeout: float) -> FakeResponse:
        nonlocal count
        count += 1
        if count == 1:
            return FakeResponse(json.dumps({"token": "t" * 32}).encode())
        return FakeResponse(
            b"different manifest",
            {
                "Docker-Content-Digest": DIGEST,
                "Content-Type": "application/vnd.oci.image.index.v1+json",
            },
        )

    with pytest.raises(EdgeRegistryError, match="does not match"):
        verify_anonymous_ghcr_pull(IMAGE, opener=opener)  # type: ignore[arg-type]
