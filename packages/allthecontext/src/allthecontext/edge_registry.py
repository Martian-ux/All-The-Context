"""Anonymous GHCR verification for a digest-addressed hosted Edge image."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .edge_distribution import image_digest, validate_image_reference

OCI_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
MAX_TOKEN_RESPONSE_BYTES = 128 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024


class ResponseLike(Protocol):
    headers: Any

    def __enter__(self) -> ResponseLike: ...

    def __exit__(self, *_args: object) -> None: ...

    def read(self, amount: int = -1) -> bytes: ...


OpenUrl = Callable[[Request, float], ResponseLike]


class EdgeRegistryError(RuntimeError):
    """The exact image was not anonymously retrievable and verifiable."""


@dataclass(frozen=True, slots=True)
class AnonymousPullEvidence:
    image_reference: str
    manifest_digest: str
    manifest_media_type: str
    manifest_bytes: int

    def mapping(self) -> dict[str, object]:
        return {
            "anonymous_pull": "passed",
            "image_reference": self.image_reference,
            "manifest_bytes": self.manifest_bytes,
            "manifest_digest": self.manifest_digest,
            "manifest_media_type": self.manifest_media_type,
        }


def _default_open(request: Request, timeout: float) -> ResponseLike:
    return cast(ResponseLike, urlopen(request, timeout=timeout))


def _bounded_read(response: ResponseLike, maximum: int, description: str) -> bytes:
    payload = response.read(maximum + 1)
    if len(payload) > maximum:
        raise EdgeRegistryError(f"GHCR {description} exceeded the safe response limit")
    return payload


def verify_anonymous_ghcr_pull(
    image_reference: str,
    *,
    timeout_seconds: float = 20.0,
    opener: OpenUrl = _default_open,
) -> AnonymousPullEvidence:
    """Fetch and hash an exact GHCR manifest without using local Docker credentials."""

    reference = validate_image_reference(image_reference)
    repository, digest = reference.removeprefix("ghcr.io/").rsplit("@", maxsplit=1)
    token_url = "https://ghcr.io/token?" + urlencode(
        {"scope": f"repository:{repository}:pull", "service": "ghcr.io"}
    )
    token_request = Request(
        token_url,
        headers={"Accept": "application/json", "User-Agent": "all-the-context-edge-verifier/1"},
    )
    try:
        with opener(token_request, timeout_seconds) as response:
            raw_token = _bounded_read(response, MAX_TOKEN_RESPONSE_BYTES, "token response")
        parsed_token = json.loads(raw_token)
        token = parsed_token.get("token") if isinstance(parsed_token, dict) else None
        if not isinstance(token, str) or len(token) < 20 or len(token) > 16_384:
            raise EdgeRegistryError("GHCR did not grant an anonymous pull token")

        manifest_request = Request(
            f"https://ghcr.io/v2/{repository}/manifests/{digest}",
            headers={
                "Accept": OCI_ACCEPT,
                "Authorization": f"Bearer {token}",
                "User-Agent": "all-the-context-edge-verifier/1",
            },
        )
        with opener(manifest_request, timeout_seconds) as response:
            manifest = _bounded_read(response, MAX_MANIFEST_BYTES, "manifest")
            reported_digest = str(response.headers.get("Docker-Content-Digest", "")).lower()
            media_type = str(response.headers.get("Content-Type", "")).split(";", maxsplit=1)[0]
    except EdgeRegistryError:
        raise
    except Exception as exc:
        raise EdgeRegistryError("GHCR image is not anonymously retrievable") from exc

    computed_digest = f"sha256:{hashlib.sha256(manifest).hexdigest()}"
    expected_digest = image_digest(reference)
    if computed_digest != expected_digest or reported_digest != expected_digest:
        raise EdgeRegistryError("GHCR returned a manifest that does not match the pinned digest")
    if media_type not in OCI_ACCEPT.split(", "):
        raise EdgeRegistryError("GHCR returned an unsupported manifest media type")
    return AnonymousPullEvidence(reference, expected_digest, media_type, len(manifest))
