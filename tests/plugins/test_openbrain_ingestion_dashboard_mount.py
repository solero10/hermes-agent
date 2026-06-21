"""Dashboard discovery and mount tests for the OpenBrain ingestion plugin."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_JS_PATH = REPO_ROOT / "plugins" / "openbrain_ingestion" / "dashboard" / "dist" / "index.js"


def _web_server_module():
    from hermes_cli import web_server

    return web_server


def test_plugin_manifest_is_discoverable_by_dashboard_server():
    web_server = _web_server_module()

    plugins = web_server._discover_dashboard_plugins()
    plugin = next((item for item in plugins if item.get("name") == "openbrain_ingestion"), None)

    assert plugin is not None
    assert plugin["has_api"] is True
    assert plugin["_api_file"] == "plugin_api.py"


def test_plugin_api_route_is_registered_on_dashboard_app():
    web_server = _web_server_module()
    route_path = "/api/plugins/openbrain_ingestion/source-types"

    route_paths = {getattr(route, "path", None) for route in web_server.app.routes}
    assert route_path in route_paths

    with TestClient(web_server.app) as client:
        response = client.get(
            route_path,
            headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
        )

    assert response.status_code in (200, 401)
    if response.status_code == 200:
        assert "source_types" in response.json()


def test_frontend_uses_dashboard_authenticated_sdk_fetch():
    assert FRONTEND_JS_PATH.exists(), f"missing frontend bundle: {FRONTEND_JS_PATH}"
    js = FRONTEND_JS_PATH.read_text(encoding="utf-8")

    assert "SDK.fetchJSON" in js
    assert "window.__HERMES_SESSION_TOKEN__" not in js
    assert not re.search(r"(?<![\w.])fetch\s*\(", js)
