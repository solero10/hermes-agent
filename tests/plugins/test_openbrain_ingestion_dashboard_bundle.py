"""Static bundle and manifest tests for the OpenBrain ingestion dashboard."""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "openbrain_ingestion"
DASHBOARD_ROOT = PLUGIN_ROOT / "dashboard"
PLUGIN_YAML_PATH = PLUGIN_ROOT / "plugin.yaml"
MANIFEST_PATH = DASHBOARD_ROOT / "manifest.json"
FRONTEND_JS_PATH = DASHBOARD_ROOT / "dist" / "index.js"
FRONTEND_CSS_PATH = DASHBOARD_ROOT / "dist" / "style.css"
DOCS_PATH = REPO_ROOT / "website" / "docs" / "user-guide" / "features" / "openbrain-ingestion-dashboard.md"


def _read(path: Path) -> str:
    assert path.exists(), f"missing file: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"empty file: {path}"
    return text


def test_manifest_and_plugin_yaml_register_dashboard_plugin():
    manifest = json.loads(_read(MANIFEST_PATH))
    assert manifest == {
        "name": "openbrain_ingestion",
        "label": "OpenBrain",
        "description": "Read-only ingestion dashboard for OpenBrain source units, lineage stages, and CortexDB receipts",
        "icon": "Database",
        "version": "0.1.0",
        "tab": {"path": "/openbrain", "position": "after:kanban"},
        "entry": "dist/index.js",
        "css": "dist/style.css",
        "api": "plugin_api.py",
    }

    plugin_yaml = _read(PLUGIN_YAML_PATH)
    assert "name: openbrain_ingestion" in plugin_yaml
    assert "kind: dashboard" in plugin_yaml
    assert "version: 0.1.0" in plugin_yaml
    assert "description:" in plugin_yaml
    assert "author: Hermes Agent" in plugin_yaml


def test_plugin_bundle_files_exist_and_are_non_empty():
    for path in (PLUGIN_YAML_PATH, MANIFEST_PATH, FRONTEND_JS_PATH, FRONTEND_CSS_PATH, DOCS_PATH):
        assert path.exists(), f"missing {path}"
        assert path.stat().st_size > 0, f"empty {path}"


def test_frontend_uses_plugin_sdk_fetch_json_auth_contract_only():
    frontend = _read(FRONTEND_JS_PATH)

    assert frontend.lstrip().startswith("(function ()")
    assert "window.__HERMES_PLUGIN_SDK__" in frontend
    assert 'window.__HERMES_PLUGINS__.register("openbrain_ingestion", OpenBrainIngestionPage);' in frontend
    assert "SDK.fetchJSON" in frontend
    assert "__HERMES_SESSION_TOKEN__" not in frontend
    assert not re.search(r"(?<![\w.])fetch\s*\(", frontend)
    assert "Authorization" not in frontend


def test_frontend_declares_all_required_components_before_registration():
    frontend = _read(FRONTEND_JS_PATH)
    registration_index = frontend.index('window.__HERMES_PLUGINS__.register("openbrain_ingestion"')
    components = [
        "Header",
        "Toolbar",
        "BoardView",
        "Metrics",
        "SourceRow",
        "StageColumn",
        "ThoughtCard",
        "ThoughtDetail",
        "SourceContext",
        "LineageTimeline",
        "ImportedReceipt",
        "StoppedReceipt",
        "RelatedMemories",
        "stopCodeLabel",
        "stopTargetText",
    ]
    for component in components:
        definition = f"function {component}"
        assert definition in frontend
        assert frontend.index(definition) < registration_index


def test_frontend_calls_expected_plugin_api_routes_with_query_params():
    frontend = _read(FRONTEND_JS_PATH)

    assert 'API_BASE + "/source-types"' in frontend
    assert 'API_BASE + "/board?source_type="' in frontend
    assert '"&filter="' in frontend
    assert '"&sort="' in frontend
    assert '"&search="' in frontend
    assert 'API_BASE + "/source-units/"' in frontend
    assert '"/thoughts/"' in frontend
    assert "source_unit_id" in frontend
    assert "lineage_id" in frontend


def test_toolbar_accessibility_board_labels_and_read_only_placeholders_present():
    frontend = _read(FRONTEND_JS_PATH)

    for label in (
        "Source type",
        "Search",
        "All",
        "Needs review",
        "Stopped",
        "Imported",
        "Zero thoughts",
        "Newest first",
        "Oldest first",
        "Most thoughts",
        "Most stopped",
    ):
        assert label in frontend

    for accessible_token in ('"aria-pressed"', '"aria-expanded"', '"aria-controls"', 'role: "dialog"', '"aria-modal": "true"'):
        assert accessible_token in frontend

    for placeholder in (
        "Open source",
        "Open CortexDB record",
        "Copy final memory",
        "Open matched memory",
        "Copy merge note",
        "Reopen later",
        "Promote later",
        "Mark for review later",
    ):
        assert placeholder in frontend

    assert "Read-only MVP" in frontend
    assert "disabled: true" in frontend
    assert "safeText(stopCodeLabel(card.stop_code), \"Stopped\")" not in frontend
    assert "No mutation" not in frontend  # action buttons are placeholders, not hidden mutation controls.


def test_board_detail_and_state_strings_are_present():
    frontend = _read(FRONTEND_JS_PATH)

    for text in (
        "No durable thoughts extracted",
        "Details →",
        "Lineage ID",
        "Candidate ID",
        "Ready for CortexDB",
        "CortexDB receipt",
        "Related memories",
        "Stopped reason",
        "Stop code",
        "Stop target",
        "Stop stage",
        "Reference / merge",
        "Duplicate of",
        "Merged into",
        "Not reached",
        "No board data loaded",
        "No source units match this filter",
        "Loading",
        'role: "alert"',
    ):
        assert text in frontend


def test_css_uses_ob_namespace_and_required_selectors():
    css = _read(FRONTEND_CSS_PATH)

    for selector in (".ob-ingestion", ".ob-toolbar", ".ob-row", ".ob-stage", ".ob-card-badges"):
        assert selector in css
    assert "ready-for-cortexdb" in css
    assert ":disabled" in css or "[disabled]" in css
    assert "@media" in css
    assert "hsl(var(--" in css

    class_selectors = re.findall(r"(^|[\s,{>])\.([a-zA-Z0-9_-]+)", css)
    offenders = sorted({name for _prefix, name in class_selectors if not name.startswith("ob-")})
    assert offenders == []


def test_docs_cover_snapshot_boundary_and_read_only_mvp():
    docs = _read(DOCS_PATH)
    for text in (
        "source type",
        "each thought card appears exactly once",
        "Ready for CortexDB",
        "Stopped and not-imported cards",
        "~/.hermes/openbrain-ingestion-dashboard/snapshot.json",
        "Read-only MVP",
        "Panning-for-Gold artifacts -> SnapshotV1",
    ):
        assert text in docs
