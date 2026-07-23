"""Read-only GitHub release preflight for immutable, single-use candidates."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, cast

from allthecontext.release_manifest import ManifestError, ReleaseVersion

REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
COMMIT = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class GitHubReader:
    repository: str
    token: str
    api_url: str = "https://api.github.com"

    def get(self, endpoint: str, *, missing_ok: bool = False) -> dict[str, Any] | None:
        url = f"{self.api_url.rstrip('/')}/repos/{self.repository}/{endpoint.lstrip('/')}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "all-the-context-release-preflight",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                value = json.load(response)
        except urllib.error.HTTPError as exc:
            if missing_ok and exc.code == 404:
                return None
            raise ManifestError(f"GitHub release preflight failed with HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ManifestError(
                "GitHub release preflight could not read trusted repository state"
            ) from exc
        if not isinstance(value, dict):
            raise ManifestError("GitHub release preflight returned an unexpected response")
        return cast(dict[str, Any], value)


def preflight_candidate(
    *,
    repository: str,
    version: str,
    source_commit: str,
    token: str,
    api_url: str,
    require_default_head: bool = True,
) -> None:
    if REPOSITORY.fullmatch(repository) is None:
        raise ManifestError("GitHub repository must be OWNER/REPOSITORY")
    ReleaseVersion.parse(version)
    if COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("candidate source commit must be a full lowercase SHA")
    if not token:
        raise ManifestError("GitHub token is required for fail-closed release preflight")
    tag = f"v{version}"
    reader = GitHubReader(repository, token, api_url)
    immutable = reader.get("immutable-releases")
    if immutable is None or immutable.get("enabled") is not True:
        raise ManifestError("repository release immutability must be enabled by an operator")
    if require_default_head:
        metadata = reader.get("")
        default_branch = metadata.get("default_branch") if metadata is not None else None
        if not isinstance(default_branch, str) or not default_branch:
            raise ManifestError("repository default branch is unavailable")
        encoded_branch = urllib.parse.quote(default_branch, safe="")
        branch = reader.get(f"branches/{encoded_branch}")
        commit = branch.get("commit") if branch is not None else None
        if not isinstance(commit, dict) or commit.get("sha") != source_commit:
            raise ManifestError("candidate source must equal the current default-branch head")
    encoded_tag = urllib.parse.quote(tag, safe="")
    if reader.get(f"releases/tags/{encoded_tag}", missing_ok=True) is not None:
        raise ManifestError("release version already has a GitHub Release and cannot be reused")
    if reader.get(f"git/ref/tags/{encoded_tag}", missing_ok=True) is not None:
        raise ManifestError("release version already has a Git tag and cannot be reused")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument(
        "--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com")
    )
    parser.add_argument(
        "--allow-default-branch-advance",
        action="store_true",
        help="recheck immutability and the unused tag without requiring main to remain stationary",
    )
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        preflight_candidate(
            repository=arguments.repository,
            version=arguments.version,
            source_commit=arguments.source_commit,
            token=os.environ.get(arguments.token_env, ""),
            api_url=arguments.api_url,
            require_default_head=not arguments.allow_default_branch_advance,
        )
        print("validated immutable, unused GitHub release slot at the default-branch head")
        return 0
    except ManifestError as exc:
        raise SystemExit(f"GitHub release gate error: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
