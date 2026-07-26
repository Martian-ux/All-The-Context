"""Browser handoff security: tickets, headers, revoke, no long-lived credential leakage."""

from __future__ import annotations

import re
from pathlib import Path

from allthecontext.browser_session import BROWSER_AUTH_SCHEME, DASHBOARD_REQUEST_HEADER
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from fastapi.testclient import TestClient


def test_browser_handoff_headers_replay_expiry_and_revoke(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with TestClient(create_app(config)) as client:
        setup = client.post("/v1/setup", json={"name": "Owner", "scopes": []})
        assert setup.status_code == 200, setup.text
        owner_token = setup.json()["token"]
        owner = {"Authorization": f"Bearer {owner_token}"}

        minted = client.post("/v1/admin/browser-session", headers=owner)
        assert minted.status_code == 200, minted.text
        connect_path = minted.json()["connect_path"]
        assert connect_path.startswith("/v1/browser/connect?ticket=")
        assert owner_token not in connect_path
        assert "Bearer" not in connect_path

        connect = client.get(connect_path)
        assert connect.status_code == 200, connect.text
        assert connect.headers.get("cache-control") == "no-store"
        assert connect.headers.get("referrer-policy") == "no-referrer"
        assert "no-referrer" in connect.text
        assert owner_token not in connect.text
        match = re.search(r'sessionStorage\.setItem\("[^"]+",\s*"([^"]+)"\)', connect.text)
        assert match is not None
        browser_token = match.group(1)
        assert browser_token != owner_token
        assert owner_token not in browser_token

        # Ticket non-replay
        replay = client.get(connect_path)
        assert replay.status_code == 410

        browser_headers = {
            "Authorization": f"{BROWSER_AUTH_SCHEME} {browser_token}",
            DASHBOARD_REQUEST_HEADER: "1",
        }
        status = client.get("/v1/context/status", headers=browser_headers)
        assert status.status_code == 200, status.text

        # Browser session cannot mint another handoff ticket (no long-lived credential).
        remint = client.post("/v1/admin/browser-session", headers=browser_headers)
        assert remint.status_code in {401, 403, 409}

        # Mutations without dashboard header fail.
        bare = {"Authorization": f"{BROWSER_AUTH_SCHEME} {browser_token}"}
        denied = client.post(
            "/v1/admin/export",
            headers=bare,
            content=b'{"passphrase":"fiction-export-passphrase"}',
        )
        assert denied.status_code == 403

        # Server-side revoke clears capability.
        revoked = client.post("/v1/browser/session/revoke", headers=browser_headers)
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked"] is True
        after = client.get("/v1/context/status", headers=browser_headers)
        assert after.status_code == 401


def test_expired_ticket_cannot_connect(tmp_path: Path, monkeypatch) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with TestClient(create_app(config)) as client:
        # Force zero-lifetime tickets on the app instance.
        client.app.state.browser_tickets.lifetime_seconds = 0.0
        setup = client.post("/v1/setup", json={"name": "Owner", "scopes": []})
        owner = {"Authorization": f"Bearer {setup.json()['token']}"}
        minted = client.post("/v1/admin/browser-session", headers=owner)
        connect_path = minted.json()["connect_path"]
        # Issue already expired: consume checks expires_at <= now after pop.
        import time

        time.sleep(0.02)
        # Re-issue with zero lifetime so expire-at-now
        client.app.state.browser_tickets.lifetime_seconds = 0.0
        minted2 = client.post("/v1/admin/browser-session", headers=owner)
        path2 = minted2.json()["connect_path"]
        # lifetime 0 means expires_at == now at issue; consume rejects expired.
        expired = client.get(path2)
        assert expired.status_code == 410
        _ = connect_path
