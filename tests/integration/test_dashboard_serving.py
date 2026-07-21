from __future__ import annotations

from pathlib import Path

from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from fastapi.testclient import TestClient


def test_core_serves_bundled_dashboard_from_same_origin(tmp_path: Path) -> None:
    app = create_app(CoreConfig.in_directory(tmp_path, require_auth=False))
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "All The Context" in response.text
    assert "text/html" in response.headers["content-type"]
