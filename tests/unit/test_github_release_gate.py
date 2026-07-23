from __future__ import annotations

import io
from typing import Any

import pytest
from allthecontext.release_manifest import ManifestError

from scripts.github_release_gate import GitHubReader, preflight_candidate

SOURCE_COMMIT = "b" * 40


def _responses(*, immutable: bool = True, release_exists: bool = False) -> dict[str, Any]:
    return {
        "immutable-releases": {"enabled": immutable, "enforced_by_owner": False},
        "": {"default_branch": "main"},
        "branches/main": {"commit": {"sha": SOURCE_COMMIT}},
        "releases/tags/v0.1.0-beta.1": {"id": 1} if release_exists else None,
        "git/ref/tags/v0.1.0-beta.1": None,
    }


def test_repository_metadata_request_has_no_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def fake_urlopen(request: Any, timeout: int) -> io.BytesIO:
        assert timeout == 20
        requested_urls.append(request.full_url)
        return io.BytesIO(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    GitHubReader("example/all-the-context", "test-only-token").get("")

    assert requested_urls == ["https://api.github.com/repos/example/all-the-context"]


def test_release_preflight_requires_immutable_unused_default_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = _responses()

    def fake_get(self: GitHubReader, endpoint: str, *, missing_ok: bool = False) -> Any:
        del self, missing_ok
        return responses[endpoint]

    monkeypatch.setattr(GitHubReader, "get", fake_get)
    preflight_candidate(
        repository="example/all-the-context",
        version="0.1.0-beta.1",
        source_commit=SOURCE_COMMIT,
        token="test-only-token",
        api_url="https://api.github.test",
    )

    responses["immutable-releases"] = {"enabled": False}
    with pytest.raises(ManifestError, match="immutability"):
        preflight_candidate(
            repository="example/all-the-context",
            version="0.1.0-beta.1",
            source_commit=SOURCE_COMMIT,
            token="test-only-token",
            api_url="https://api.github.test",
        )


def test_release_preflight_rejects_reused_version_and_can_recheck_after_main_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = _responses(release_exists=True)

    def fake_get(self: GitHubReader, endpoint: str, *, missing_ok: bool = False) -> Any:
        del self, missing_ok
        return responses[endpoint]

    monkeypatch.setattr(GitHubReader, "get", fake_get)
    with pytest.raises(ManifestError, match="cannot be reused"):
        preflight_candidate(
            repository="example/all-the-context",
            version="0.1.0-beta.1",
            source_commit=SOURCE_COMMIT,
            token="test-only-token",
            api_url="https://api.github.test",
        )

    responses["releases/tags/v0.1.0-beta.1"] = None
    responses["branches/main"] = {"commit": {"sha": "c" * 40}}
    preflight_candidate(
        repository="example/all-the-context",
        version="0.1.0-beta.1",
        source_commit=SOURCE_COMMIT,
        token="test-only-token",
        api_url="https://api.github.test",
        require_default_head=False,
    )


def test_release_preflight_can_use_external_operator_immutability_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = _responses()

    def fake_get(self: GitHubReader, endpoint: str, *, missing_ok: bool = False) -> Any:
        del self, missing_ok
        if endpoint == "immutable-releases":
            raise AssertionError("Actions must not call the admin-only immutability endpoint")
        return responses[endpoint]

    monkeypatch.setattr(GitHubReader, "get", fake_get)
    preflight_candidate(
        repository="example/all-the-context",
        version="0.1.0-beta.1",
        source_commit=SOURCE_COMMIT,
        token="test-only-token",
        api_url="https://api.github.test",
        require_immutability_api=False,
    )
