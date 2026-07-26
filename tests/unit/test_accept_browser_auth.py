from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_acceptance_module():
    path = ROOT / "scripts" / "accept_browser_auth.py"
    spec = importlib.util.spec_from_file_location("accept_browser_auth", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


acceptance = _load_acceptance_module()


def _handoff_html(
    token: str = "opaque-session",
    *,
    nonce: str = "fixed",
    storage_key: str = "atc.browserSession",
    target: str = "/",
    extra_attributes: str = "",
) -> str:
    return (
        f'<script nonce="{nonce}" data-storage-key="{storage_key}"'
        f' data-browser-token="{token}" data-dashboard-target="{target}"'
        f"{extra_attributes}>"
        "const handoff=document.currentScript;"
        "sessionStorage.setItem(handoff.dataset.storageKey,handoff.dataset.browserToken);"
        "window.location.replace(handoff.dataset.dashboardTarget);"
        "</script>"
    )


def _handoff_csp(nonce: str = "fixed") -> str:
    return (
        "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
        f"script-src 'nonce-{nonce}'"
    )


def _parse(handoff_html: str, csp: str | None = None) -> str | None:
    return acceptance.browser_session_from_handoff_html(
        handoff_html,
        _handoff_csp() if csp is None else csp,
    )


def test_browser_session_reads_escaped_adr064_data_attribute() -> None:
    assert _parse(_handoff_html("opaque-session-&quot;&amp;&lt;canary&gt;")) == (
        'opaque-session-"&<canary>'
    )


@pytest.mark.parametrize(
    "handoff_html",
    [
        '<script>sessionStorage.setItem("atc.browserSession","executable-secret")</script>',
        _handoff_html().replace(
            "window.location.replace(handoff.dataset.dashboardTarget);",
            'window.location.replace("/");',
        ),
        _handoff_html("one") + _handoff_html("two"),
        (
            '<body data-storage-key="atc.browserSession" data-browser-token="body-secret"'
            ' data-dashboard-target="/"></body>'
        ),
        _handoff_html()
        + '<script>sessionStorage.setItem("atc.browserSession","executable-secret")</script>',
        _handoff_html().replace(' nonce="fixed"', ""),
        _handoff_html(nonce=""),
        _handoff_html(extra_attributes=' src="/assets/handoff.js"'),
        f"<template>{_handoff_html()}</template>",
        _handoff_html(storage_key="wrong"),
        _handoff_html(extra_attributes=' nonce="other"'),
    ],
)
def test_browser_session_rejects_ambiguous_or_nonproduction_markup(
    handoff_html: str,
) -> None:
    assert _parse(handoff_html) is None


@pytest.mark.parametrize("csp", ["", _handoff_csp("wrong")])
def test_browser_session_requires_matching_csp_nonce(csp: str) -> None:
    assert _parse(_handoff_html(), csp) is None


@pytest.mark.parametrize(
    "target",
    [
        "/",
        "/?page=sources",
        "/?page=context",
        "/?page=connections",
        "/?page=activity",
        "/?page=backup",
        "/?page=updates",
    ],
)
def test_browser_session_accepts_only_production_targets(target: str) -> None:
    assert _parse(_handoff_html(target=target)) == "opaque-session"


@pytest.mark.parametrize(
    "target",
    ["/wrong", "//example.test", "/?page=unknown", "https://example.test/"],
)
def test_browser_session_rejects_wrong_targets(target: str) -> None:
    assert _parse(_handoff_html(target=target)) is None
