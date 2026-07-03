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
    assert {k: v for k, v in manifest.items() if k not in {"entry", "css"}} == {
        "name": "openbrain_ingestion",
        "label": "OpenBrain",
        "description": "Read-only ingestion dashboard for OpenBrain source units, lineage stages, and CortexDB receipts",
        "icon": "Database",
        "version": "0.1.0",
        "tab": {"path": "/openbrain", "position": "after:kanban"},
        "api": "plugin_api.py",
    }
    assert manifest["entry"].startswith("dist/index.js")
    assert manifest["css"].startswith("dist/style.css")

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
        "PolicyTag",
        "PolicyDefinitionDialog",
        "SourceRow",
        "StageColumn",
        "ThoughtCard",
        "ThoughtDetail",
        "SourceContext",
        "LineageTimeline",
        "FormationTrace",
        "FormationLineageCards",
        "FormationAdditionalContext",
        "FormationGateEvents",
        "TraceTextBlock",
        "TraceKeyValueList",
        "ImportedReceipt",
        "StoppedReceipt",
        "RelatedMemories",
        "DatabaseFields",
        "DatabaseFieldValue",
        "StageSpecificDetailPanel",
        "ExtractedEvidencePanel",
        "ShapedFormationPanel",
        "PolicyDecisionPanel",
        "DedupeEvidencePanel",
        "ReadyPackagePanel",
        "CortexDBReceiptPanel",
        "StageKeyValueList",
        "StageChecklist",
        "stopCodeLabel",
        "stopTargetText",
    ]
    for component in components:
        definition = f"function {component}"
        assert definition in frontend
        assert frontend.index(definition) < registration_index
    assert "function PolicyLegend" not in frontend
    assert "ob-policy-legend" not in frontend


def test_frontend_calls_expected_plugin_api_routes_with_query_params():
    frontend = _read(FRONTEND_JS_PATH)

    assert 'API_BASE + "/source-types"' in frontend
    assert 'API_BASE + "/board?source_type="' in frontend
    assert '"&filter="' in frontend
    assert '"&sort="' in frontend
    assert '"&search="' in frontend
    assert '"&date_from="' in frontend
    assert '"&date_to="' in frontend
    assert "sourceDateQueryValue(dateFrom)" in frontend
    assert "sourceDateInputSameDay(value)" in frontend
    assert "onDateFromChange: handleDateFromChange" in frontend
    assert 'API_BASE + "/source-units/"' in frontend
    assert '"/thoughts/"' in frontend
    assert "source_unit_id" in frontend
    assert "lineage_id" in frontend


def test_toolbar_accessibility_board_labels_and_read_only_placeholders_present():
    frontend = _read(FRONTEND_JS_PATH)

    for label in (
        "Source type",
        "Search",
        "Source date from",
        "Source date to",
        "MM/DD/YYYY",
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
        "Copy trace summary",
        "Copy LLM input",
        "Copy shaped output",
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


def test_frontend_source_date_from_auto_fills_empty_to_same_date_only():
    frontend = _read(FRONTEND_JS_PATH)

    assert "function handleDateFromChange(value)" in frontend
    assert "setDateFrom(value);" in frontend
    assert "setDateTo(function (current)" in frontend
    assert "if (String(current || \"\").trim()) return current;" in frontend
    assert "return sourceDateInputSameDay(value) || current;" in frontend
    assert 'return padDatePart(date.getUTCMonth() + 1) + "/" + padDatePart(date.getUTCDate()) + "/" + date.getUTCFullYear();' in frontend


def test_board_detail_and_state_strings_are_present():
    frontend = _read(FRONTEND_JS_PATH)

    for text in (
        "No durable thoughts extracted",
        "Source date:",
        "Details →",
        "Lineage ID",
        "Candidate ID",
        "Formation trace",
        "Primary lineage cards used",
        "Additional context consulted",
        "Exact LLM input sent to shaping step",
        "Output produced by shaping step",
        "Gate decisions",
        "traceOutputTitleDistinct(trace, thought)",
        "formationTraceHasContent(trace)",
        "item.summary ? h(\"p\"",
        "item.quote || item.source_snippet",
        "TraceKeyValueList",
        "stageDetailFor(thought, \"shaped\")",
        "Extracted evidence",
        "Evidence status",
        "Used by Thought candidates",
        "Policy decision",
        "Candidate text reviewed",
        "Dedupe evidence",
        "Semantic dedupe evidence",
        "Ready package",
        "Import payload preview",
        "What actually got stored?",
        "not hidden model reasoning",
        "No formation trace in this snapshot.",
        "Ready for CortexDB",
        "CortexDB receipt",
        "Related memories",
        "Database fields",
        "public.thoughts",
        "Actual CortexDB/OpenBrain thought-table columns",
        "A dash means this dashboard snapshot does not have a value for that field.",
        "No value in snapshot",
        "database_fields",
        "Stopped reason",
        "Policy tag",
        "Policy tag definition",
        "Click for policy definition",
        "Stop target",
        "Stop stage",
        "Needs source validation",
        "Sensitive detail",
        "Stale task",
        "Obsolete internal process",
        "Too thin / missing context",
        "No durable value",
        "Evidence is weak, outline-only, voicemail-derived, or ambiguous.",
        "raw private identifier",
        "Duplicate of",
        "Merged into",
        "Not reached",
        "No board data loaded",
        "No source units match this filter",
        "Loading",
        'role: "alert"',
    ):
        assert text in frontend


def test_abbreviated_thought_cards_do_not_render_stage_or_lineage_metadata():
    frontend = _read(FRONTEND_JS_PATH)
    card_start = frontend.index("function ThoughtCard")
    card_end = frontend.index("function SourceContext")
    thought_card = frontend[card_start:card_end]

    assert "ob-card-meta" not in frontend
    assert 'h("dt", null, "Stage")' not in thought_card
    assert 'h("dt", null, "Lineage")' not in thought_card
    assert "safeText(card.lineage_id || card.id)" not in thought_card
    assert "effectiveStopCode(card)" in thought_card
    assert "isDuplicateText(card.summary, card.stopped_reason)" in thought_card
    assert "h(PolicyTag" in thought_card
    assert "Lineage ID" in frontend  # detail view keeps the lineage identifier.


def test_evidence_cards_keep_badge_tags_and_open_details_from_card_face():
    frontend = _read(FRONTEND_JS_PATH)
    css = _read(FRONTEND_CSS_PATH)
    card_start = frontend.index("function ThoughtCard")
    card_end = frontend.index("function SourceContext")
    thought_card = frontend[card_start:card_end]

    assert 'const isEvidenceCard = card.current_stage === "extracted";' in thought_card
    assert 'isEvidenceCard ? "ob-thought-card--clickable" : null' in thought_card
    assert 'cardProps.role = "button";' in thought_card
    assert "cardProps.tabIndex = 0;" in thought_card
    assert "cardProps.onClick = function () { props.onOpenDetail(card); };" in thought_card
    assert 'event.key === "Enter" || event.key === " "' in thought_card
    assert 'h("span", { className: cx("ob-badge", "ob-badge--" + stageSlug(disposition)) }, dispositionLabel(disposition))' in thought_card
    assert 'String(topic || "").trim().toLowerCase() !== "extracted evidence"' in thought_card
    assert 'cardTopics.length ? h("div", { className: "ob-topic-list" }, cardTopics.map(function (topic)' in thought_card
    assert 'cardSummary && !isEvidenceCard ? h("p", { className: "ob-card-summary" }, cardSummary) : null' in thought_card
    assert 'card.stopped_reason && !isEvidenceCard ? h("p", { className: "ob-card-reason" }' in thought_card
    assert '!isEvidenceCard ? h("button", {' in thought_card
    assert "ob-thought-card--clickable" in css
    assert ".ob-thought-card--clickable:focus-visible" in css


def test_database_fields_are_wired_into_detail_bottom_and_render_field_payloads():
    frontend = _read(FRONTEND_JS_PATH)
    detail_start = frontend.index("function ThoughtDetail")
    detail_end = frontend.index("function OpenBrainIngestionPage")
    detail = frontend[detail_start:detail_end]

    assert "h(RelatedMemories, { thought: thought })" in detail
    assert "h(DatabaseFields, { thought: thought })" in detail
    assert "h(StageSpecificDetailPanel" in detail
    assert detail.index("h(StageSpecificDetailPanel") < detail.index("h(SourceContext, { thought: thought })")
    assert detail.index("h(SourceContext, { thought: thought })") < detail.index(
        "h(LineageTimeline, { thought: thought })"
    )
    assert detail.index("h(RelatedMemories, { thought: thought })") < detail.index(
        "h(DatabaseFields, { thought: thought })"
    )

    fields_start = frontend.index("function DatabaseFields")
    fields_end = frontend.index("function ThoughtDetail")
    database_fields = frontend[fields_start:fields_end]

    for required in (
        "props.thought && props.thought.database_fields",
        'h("dl", { className: "ob-db-field-grid" }',
        'h("dt", { className: "ob-db-field-key" }',
        'h("dd", { className: "ob-db-field-value" }',
        "safeText(field.name)",
        "field.description",
        "h(DatabaseFieldValue, { field: field })",
        "field.note",
    ):
        assert required in database_fields


def test_css_uses_ob_namespace_and_required_selectors():
    css = _read(FRONTEND_CSS_PATH)

    for selector in (".ob-ingestion", ".ob-toolbar", ".ob-row", ".ob-stage", ".ob-card-badges", ".ob-policy-tag", ".ob-policy-definition-dialog", ".ob-formation-trace", ".ob-trace-card", ".ob-trace-pre", ".ob-trace-kv-list", ".ob-stage-detail-panel", ".ob-stage-checklist", ".ob-database-fields", ".ob-db-field-row"):
        assert selector in css
    assert "ready-for-cortexdb" in css
    assert ":disabled" in css or "[disabled]" in css
    assert "@media" in css
    assert "hsl(var(--" in css

    assert "grid-template-columns: minmax(0, 1fr) max-content;" not in css
    snapshot_meta_css = css[css.index(".ob-snapshot-meta {"):css.index(".ob-toolbar {")]
    assert "grid-template-columns: minmax(0, 1fr) minmax(12rem, 24rem);" in css
    assert "min-width: 0;" in snapshot_meta_css
    assert "max-width: 24rem;" in snapshot_meta_css
    assert ".ob-snapshot-meta dd {" in snapshot_meta_css
    assert "text-overflow: ellipsis;" in snapshot_meta_css
    assert "white-space: nowrap;" in snapshot_meta_css

    class_selectors = re.findall(r"(^|[\s,{>])\.([a-zA-Z0-9_-]+)", css)
    offenders = sorted({name for _prefix, name in class_selectors if not name.startswith("ob-")})
    assert offenders == []


def test_docs_cover_snapshot_boundary_and_read_only_mvp():
    docs = _read(DOCS_PATH)
    for text in (
        "source type",
        "each card appears exactly once",
        "Evidence cards",
        "Thought candidates",
        "Ready for CortexDB",
        "Stopped and not-imported cards",
        "~/.hermes/openbrain-ingestion-dashboard/snapshot.json",
        "Read-only MVP",
        "Panning-for-Gold artifacts -> SnapshotV1",
        "Policy tags use this vocabulary",
        "Needs source validation",
        "Formation Trace",
        "exact LLM input package",
        "Column-specific detail",
        "Evidence card detail",
        "Thought candidate detail",
        "Policy detail",
        "Dedupe detail",
        "Ready detail",
        "CortexDB detail",
    ):
        assert text in docs
