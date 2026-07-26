from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_acceptance_module():
    path = ROOT / "scripts" / "accept_browser_auth.py"
    spec = importlib.util.spec_from_file_location("accept_browser_auth", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


acceptance = _load_acceptance_module()


def _handoff_html(token: str) -> str:
    return (
        '<script nonce="fixed" data-storage-key="atc.browserSession"'
        f' data-browser-token="{token}" data-dashboard-target="/">'
        "const handoff=document.currentScript;"
        "sessionStorage.setItem(handoff.dataset.storageKey,handoff.dataset.browserToken);"
        "window.location.replace(handoff.dataset.dashboardTarget);"
        "</script>"
    )


def test_browser_session_reads_escaped_adr064_data_attribute() -> None:
    assert acceptance.browser_session_from_handoff_html(
        _handoff_html("opaque-session-&quot;&amp;&lt;canary&gt;")
    ) == 'opaque-session-"&<canary>'


def test_browser_session_rejects_executable_literals_and_ambiguous_markup() -> None:
    obsolete_literal = (
        '<script>sessionStorage.setItem("atc.browserSession","executable-secret")</script>'
    )
    wrong_script = _handoff_html("opaque-session").replace(
        "window.location.replace(handoff.dataset.dashboardTarget);",
        'window.location.replace("/");',
    )
    duplicate = _handoff_html("one") + _handoff_html("two")
    non_script_attribute = (
        '<body data-storage-key="atc.browserSession" data-browser-token="body-secret"'
        ' data-dashboard-target="/"></body>'
    )

    assert acceptance.browser_session_from_handoff_html(obsolete_literal) is None
    assert acceptance.browser_session_from_handoff_html(wrong_script) is None
    assert acceptance.browser_session_from_handoff_html(duplicate) is None
    assert acceptance.browser_session_from_handoff_html(non_script_attribute) is None
