"""Dashboard discovery and mount tests for the OpenBrain ingestion plugin."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "openbrain_ingestion"
FRONTEND_JS_PATH = PLUGIN_ROOT / "dashboard" / "dist" / "index.js"


def _run_dashboard_probe(tmp_path: Path, *, install_user_copy: bool) -> dict:
    """Import the dashboard server in a fresh process with a copy-backed home.

    The real upgrade must preserve ``source=user`` when a runtime user plugin is
    present.  Importing ``hermes_cli.web_server`` in-process is not enough here:
    the module mounts plugin API routes at import time, and Ken's live default
    ``~/.hermes/plugins`` tree can otherwise leak into this test.  A subprocess
    with a temporary HERMES_HOME makes the source contract explicit and keeps the
    live user plugin directory read-only.
    """

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    if install_user_copy:
        user_plugin = hermes_home / "plugins" / "openbrain-ingestion"
        shutil.copytree(
            PLUGIN_ROOT,
            user_plugin,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
        (hermes_home / "config.yaml").write_text(
            "plugins:\n  enabled:\n    - openbrain_ingestion\n",
            encoding="utf-8",
        )

    script = textwrap.dedent(
        """
        import json
        from fastapi.testclient import TestClient
        from hermes_cli import web_server

        plugin = next(
            item for item in web_server._discover_dashboard_plugins()
            if item.get("name") == "openbrain_ingestion"
        )
        route_path = "/api/plugins/openbrain_ingestion/source-types"
        def app_route_paths(app):
            paths = set()
            for route in app.routes:
                path = getattr(route, "path", None) or getattr(route, "path_format", None)
                if path:
                    paths.add(path)
                # FastAPI/Starlette 1.x keeps included routers as lazy
                # _IncludedRouter entries; flatten them for static route
                # contract tests instead of relying on the older .path attr.
                original = getattr(route, "original_router", None)
                include_context = getattr(route, "include_context", None)
                prefix = getattr(include_context, "prefix", "") if include_context else ""
                if original is not None:
                    for child in getattr(original, "routes", []):
                        child_path = getattr(child, "path", None) or getattr(child, "path_format", None)
                        if child_path:
                            paths.add(prefix + child_path)
            return paths

        route_paths = app_route_paths(web_server.app)
        with TestClient(web_server.app) as client:
            response = client.get(
                route_path,
                headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
            )
        status_code = response.status_code
        try:
            payload_keys = sorted(response.json().keys())
        except Exception:
            payload_keys = []
        print(json.dumps({
            "source": plugin.get("source"),
            "has_api": plugin.get("has_api"),
            "api_file": plugin.get("_api_file"),
            "route_present": route_path in route_paths,
            "status_code": status_code,
            "payload_keys": payload_keys,
        }, sort_keys=True))
        """
    )
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
        check=True,
    )
    return json.loads(result.stdout)


def test_bundled_plugin_manifest_is_discoverable_by_dashboard_server(tmp_path):
    payload = _run_dashboard_probe(tmp_path, install_user_copy=False)

    assert payload["source"] == "bundled"
    assert payload["has_api"] is True
    assert payload["api_file"] == "plugin_api.py"
    assert payload["route_present"] is True
    assert payload["status_code"] == 200
    assert "source_types" in payload["payload_keys"]


def test_user_plugin_copy_wins_and_mounts_when_enabled(tmp_path):
    payload = _run_dashboard_probe(tmp_path, install_user_copy=True)

    assert payload["source"] == "user"
    assert payload["has_api"] is True
    assert payload["api_file"] == "plugin_api.py"
    assert payload["route_present"] is True
    assert payload["status_code"] == 200
    assert "source_types" in payload["payload_keys"]


def test_frontend_uses_dashboard_authenticated_sdk_fetch():
    assert FRONTEND_JS_PATH.exists(), f"missing frontend bundle: {FRONTEND_JS_PATH}"
    js = FRONTEND_JS_PATH.read_text(encoding="utf-8")

    assert "SDK.fetchJSON" in js
    assert "window.__HERMES_SESSION_TOKEN__" not in js
    assert not re.search(r"(?<![\w.])fetch\s*\(", js)
