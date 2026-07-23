"""Exercise the built Edge container as its non-root user before publication."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import secrets
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

CONTAINER_NAME_PATTERN = re.compile(r"atc-edge-smoke-[0-9a-f]{12}")
LOCAL_IMAGE_PATTERN = re.compile(r"[a-z0-9][a-z0-9._/-]*(?::[a-zA-Z0-9._-]+|@sha256:[0-9a-f]{64})?")


def _encoded_claim() -> str:
    def encoded(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    claim = {
        "claim_id": secrets.token_urlsafe(32),
        "encryption_public_key": encoded(secrets.token_bytes(32)),
        "expires_at": int(time.time()) + 3600,
        "owner_secret_hash": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
        "signing_public_key": encoded(secrets.token_bytes(32)),
        "vault_id": "container-release-smoke",
    }
    raw = json.dumps(claim, sort_keys=True, separators=(",", ":")).encode()
    return "atc-edge-claim-v1." + encoded(raw)


def _run(*command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, capture_output=True, text=True)


def _json_request(url: str, *, method: str = "GET") -> tuple[int, object]:
    request = Request(url, method=method, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=3.0) as response:
            return int(response.status), json.loads(response.read(256 * 1024))
    except HTTPError as exc:
        try:
            body: object = json.loads(exc.read(256 * 1024))
        except json.JSONDecodeError:
            body = None
        return exc.code, body


def smoke(image: str, *, timeout_seconds: float = 45.0) -> dict[str, object]:
    if LOCAL_IMAGE_PATTERN.fullmatch(image) is None:
        raise RuntimeError("local verification image name is invalid")
    name = f"atc-edge-smoke-{secrets.token_hex(6)}"
    if CONTAINER_NAME_PATTERN.fullmatch(name) is None:  # pragma: no cover - defensive
        raise RuntimeError("generated container name is invalid")
    claim = _encoded_claim()
    started = False
    try:
        _run(
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--publish",
            "127.0.0.1::8743",
            "--env",
            f"ATC_EDGE_BUNDLE={claim}",
            "--env",
            "ATC_EDGE_PUBLIC_URL=https://container-smoke.example.invalid",
            image,
        )
        started = True
        published = _run("docker", "port", name, "8743/tcp").stdout.strip().splitlines()
        if len(published) != 1 or not published[0].startswith("127.0.0.1:"):
            raise RuntimeError("container did not publish one loopback test port")
        port = int(published[0].rsplit(":", maxsplit=1)[1])
        origin = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + timeout_seconds
        health: object = None
        while time.monotonic() < deadline:
            try:
                status, health = _json_request(f"{origin}/healthz")
                if status == 200:
                    break
            except OSError:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("container did not become healthy before the deadline")
        if health != {
            "status": "awaiting_claim",
            "component": "edge",
            "authority": "core",
        }:
            raise RuntimeError("container did not remain inert before its authorized claim")
        if _json_request(f"{origin}/about")[0] != 423:
            raise RuntimeError("unclaimed container exposed an ordinary Edge route")
        challenge_status, challenge = _json_request(
            f"{origin}/v1/edge/claim/challenge", method="POST"
        )
        if (
            challenge_status != 200
            or not isinstance(challenge, dict)
            or not isinstance(challenge.get("challenge"), str)
        ):
            raise RuntimeError("container did not expose its bounded one-time claim route")
        user = _run("docker", "exec", name, "id", "-u").stdout.strip()
        if user != "10001":
            raise RuntimeError("container process did not run as the dedicated non-root user")
        return {
            "authority": "core",
            "claim_route": "passed",
            "image": image,
            "inert_before_claim": True,
            "non_root_uid": 10001,
            "result": "passed",
        }
    except Exception as exc:
        if started:
            logs = _run("docker", "logs", name, check=False).stdout[-8_000:]
            if logs:
                raise RuntimeError(f"{exc}\ncontainer logs:\n{logs}") from exc
        raise
    finally:
        if started and CONTAINER_NAME_PATTERN.fullmatch(name) is not None:
            _run("docker", "rm", "--force", name, check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()
    print(
        json.dumps(
            smoke(args.image, timeout_seconds=max(5.0, min(args.timeout_seconds, 120.0))),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
