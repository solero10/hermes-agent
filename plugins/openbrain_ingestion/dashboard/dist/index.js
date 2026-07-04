(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;

  const React = SDK.React;
  const h = React.createElement;
  const hooks = SDK.hooks || React;
  const useEffect = hooks.useEffect || React.useEffect;
  const useMemo = hooks.useMemo || React.useMemo;
  const useState = hooks.useState || React.useState;

  const API_BASE = "/api/plugins/openbrain_ingestion";
  const STAGES = [
    { id: "extracted", label: "Evidence cards" },
    { id: "shaped", label: "Thought candidates" },
    { id: "policy", label: "Policy" },
    { id: "deduped", label: "Deduped" },
    { id: "ready_for_cortexdb", label: "Ready for CortexDB" },
    { id: "cortexdb", label: "CortexDB" },
  ];
  const FILTERS = [
    { value: "all", label: "All" },
    { value: "needs_review", label: "Needs review" },
    { value: "stopped", label: "Stopped" },
    { value: "imported", label: "Imported" },
    { value: "zero_thoughts", label: "Zero thoughts" },
  ];
  const SORTS = [
    { value: "newest", label: "Newest first" },
    { value: "oldest", label: "Oldest first" },
    { value: "most_thoughts", label: "Most thoughts" },
    { value: "most_stopped", label: "Most stopped" },
  ];
  const READ_ONLY_TITLE = "Read-only MVP";
  const READ_ONLY_EXPLANATION = "Read-only MVP: this dashboard mirrors ingestion state only. Source, CortexDB, merge, review, and promotion actions are intentionally disabled placeholders.";
  const DEFAULT_POLICY_STOP_DEFINITIONS = [
    {
      id: "needs_source_validation",
      label: "Needs source validation",
      definition: "Evidence is weak, outline-only, voicemail-derived, or ambiguous. Verify against the source before capture.",
    },
    {
      id: "sensitive_detail",
      label: "Sensitive detail",
      definition: "Contains a raw private identifier, case/reference/account number, emergency/contact detail, or similar information that belongs in controlled evidence, not general memory.",
    },
    {
      id: "stale_task",
      label: "Stale task",
      definition: "Looks like an old action item or status update. Check current status or rewrite as history before capture.",
    },
    {
      id: "obsolete_internal",
      label: "Obsolete internal process",
      definition: "Old employer/company-specific process mechanics with no reusable lesson. Keep in source artifacts; do not capture as CortexDB memory.",
    },
    {
      id: "too_thin",
      label: "Too thin / missing context",
      definition: "Missing enough who/what/why or identifiers to be a reliable standalone memory.",
    },
    {
      id: "no_durable_value",
      label: "No durable value",
      definition: "Incidental, time-specific, already expired, or not useful enough to keep as long-term memory.",
    },
  ];

  function cx() {
    return Array.prototype.slice.call(arguments).filter(Boolean).join(" ");
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function safeText(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback || "—";
    return String(value);
  }

  function stageLabel(stageId) {
    const match = STAGES.find(function (stage) { return stage.id === stageId; });
    return match ? match.label : safeText(stageId, "Unknown stage");
  }

  function stageSlug(stageId) {
    return String(stageId || "unknown").replace(/_/g, "-").toLowerCase();
  }

  function stageClass(stageId) {
    return "ob-stage--" + stageSlug(stageId);
  }

  function columnDisplayLabel(column) {
    const canonical = stageLabel(column && column.id);
    return canonical && canonical !== "Unknown stage" ? canonical : (column && column.label) || canonical;
  }

  function dispositionLabel(value) {
    const text = String(value || "in_progress").replace(/_/g, " ");
    return text.replace(/\b\w/g, function (char) { return char.toUpperCase(); });
  }

  function stopCodeSlug(value) {
    return String(value || "").replace(/\s+/g, "_").replace(/-/g, "_").toLowerCase();
  }

  function stopCodeLabel(value) {
    const slug = stopCodeSlug(value);
    const definition = policyDefinitionFor(slug, DEFAULT_POLICY_STOP_DEFINITIONS);
    if (definition) return definition.label;
    if (!slug) return "";
    if (slug === "duplicate") return "Duplicate";
    if (slug === "reference_merge" || slug === "reference/merge") return "Needs shaping / merge";
    if (slug === "needs_current_validation") return "Needs source validation";
    if (slug === "obsolete") return "No durable value";
    if (slug === "policy") return "Too thin / missing context";
    if (slug === "non_thought") return "No durable value";
    if (slug === "needs_review") return "Needs review";
    if (slug === "other") return "Stopped";
    return slug.replace(/_/g, " ").replace(/\b\w/g, function (char) { return char.toUpperCase(); });
  }

  function stopCodeTitle(value) {
    const slug = stopCodeSlug(value);
    const definition = policyDefinitionFor(slug, DEFAULT_POLICY_STOP_DEFINITIONS);
    if (definition) return definition.definition;
    if (slug === "reference_merge" || slug === "reference/merge") {
      return "Legacy tag: useful material that should be rewritten or merged before capture. It is not a policy prohibition by itself.";
    }
    if (slug === "needs_current_validation") {
      return stopCodeTitle("needs_source_validation");
    }
    if (slug === "obsolete") {
      return stopCodeTitle("no_durable_value");
    }
    if (slug === "policy") {
      return stopCodeTitle("too_thin");
    }
    if (slug === "duplicate") return "Duplicate only after exact and semantic dedupe evidence confirms this is already covered.";
    return "";
  }

  function policyDefinitionFor(value, definitions) {
    const slug = stopCodeSlug(value);
    return asArray(definitions).find(function (item) { return stopCodeSlug(item && item.id) === slug; });
  }

  function policyDefinitions(board) {
    const definitions = asArray(board && board.policy_stop_definitions);
    return definitions.length ? definitions : DEFAULT_POLICY_STOP_DEFINITIONS;
  }

  function policyDefinitionFromCode(code, definitions) {
    const slug = stopCodeSlug(code);
    const match = policyDefinitionFor(slug, definitions);
    const label = match ? match.label : stopCodeLabel(slug);
    const definition = match ? match.definition : stopCodeTitle(slug);
    return {
      id: slug,
      label: label,
      definition: definition || "No policy definition supplied for this tag.",
    };
  }

  function PolicyTag(props) {
    const code = props.code;
    const definition = policyDefinitionFromCode(code, props.definitions);
    if (!definition.label) return null;
    return h("button", {
      type: "button",
      className: cx("ob-badge", "ob-badge--stopped", "ob-policy-tag", "ob-policy-tag-button"),
      title: "Click for policy definition",
      "aria-label": definition.label + ": click for policy definition",
      onClick: function (event) {
        event.stopPropagation();
        if (props.onShow) props.onShow(definition);
      },
    }, definition.label);
  }

  function effectiveStopCode(card) {
    if (!card) return "";
    if (stopCodeSlug(card.stop_code)) return card.stop_code;
    if (card.current_stage !== "policy") return "";
    const text = [card.title, card.summary, card.stopped_reason, asArray(card.topics).join(" ")].join(" ").toLowerCase();
    if (text.includes("verify") || text.includes("not substantiated") || text.includes("source evidence") || text.includes("source does not clarify")) {
      return "needs_source_validation";
    }
    if (text.includes("raw identifier") || text.includes("sensitive") || text.includes("case/reference") || text.includes("reference number") || text.includes("account number")) {
      return "sensitive_detail";
    }
    if (text.includes("should not be reactivated") || text.includes("current status") || text.includes("likely completed") || text.includes("stale unless")) {
      return "stale_task";
    }
    if (text.includes("internal process") || text.includes("obsolete ey") || text.includes("ey mechanics")) {
      return "obsolete_internal";
    }
    if (text.includes("no durable value") || text.includes("incidental") || text.includes("not worth retaining") || text.includes("not useful to preserve")) {
      return "no_durable_value";
    }
    return "too_thin";
  }

  function isDuplicateText(left, right) {
    const a = String(left || "").trim().replace(/\s+/g, " ");
    const b = String(right || "").trim().replace(/\s+/g, " ");
    return !!a && !!b && a === b;
  }

  function stopTargetText(thought) {
    if (!thought || !thought.stop_target_label) return "";
    const label = safeText(thought.stop_target_label, "");
    if (!label || label === "—") return "";
    const slug = String(thought.stop_code || "").toLowerCase();
    if (slug === "duplicate") return "Duplicate of " + label;
    if (slug === "reference_merge") return "Merged into " + label;
    return label;
  }

  function countOf(counts, key) {
    const value = counts && counts[key];
    return Number.isFinite(Number(value)) ? Number(value) : 0;
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatSourceDate(value) {
    if (!value) return "";
    const dateOnly = String(value).match(/^(\d{4})-(\d{2})-(\d{2})(?:T00:00:00(?:\.000)?Z)?$/);
    const date = dateOnly
      ? new Date(Date.UTC(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3])))
      : new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      timeZone: dateOnly ? "UTC" : undefined,
    });
  }

  function sourceDateText(row) {
    return formatSourceDate(row.source_date || row.occurred_at || row.processed_at);
  }

  const CANDIDATE_TABLE_STAGES = [
    { id: "tags", label: "Tags" },
    { id: "policy", label: "Policy" },
    { id: "deduped", label: "Deduped" },
    { id: "ready_for_cortexdb", label: "Ready for CortexDB" },
    { id: "cortexdb", label: "CortexDB import" },
  ];

  function cardsForStage(row, stageId) {
    const columns = (row && row.columns) || {};
    return asArray(columns[stageId]);
  }

  function evidenceCardsForRow(row) {
    return cardsForStage(row, "extracted");
  }

  function evidenceCardTitle(card) {
    return safeText(
      (card && (card.title || card.evidence_title || card.summary || card.source_snippet || card.quote || card.raw_text || card.lineage_id || card.id)),
      "Untitled Evidence"
    );
  }

  function evidenceTopicLabel(topic) {
    const text = safeText(topic, "");
    const normalized = text.trim().toLowerCase();
    if (normalized === "covered by thought candidate") return "used";
    return text;
  }

  function candidateCardsForRow(row) {
    const columns = (row && row.columns) || {};
    const cards = [];
    Object.keys(columns).forEach(function (stageId) {
      if (stageId !== "extracted") {
        asArray(columns[stageId]).forEach(function (card) { cards.push(card); });
      }
    });
    return cards;
  }

  function stageIndex(stageId) {
    const normalized = String(stageId || "").replace(/-/g, "_");
    return STAGES.findIndex(function (stage) { return stage.id === normalized; });
  }

  function candidateStageStatus(card, stageId) {
    const currentStage = card && card.current_stage ? card.current_stage : "shaped";
    const disposition = card && card.disposition ? card.disposition : "in_progress";
    const targetIndex = stageIndex(stageId);
    const currentIndex = stageIndex(currentStage);

    if (stageId === "tags") return "metadata";
    if (stageId === "cortexdb") {
      return disposition === "imported" && card.cortexdb_id ? "imported" : "not_imported";
    }
    if (disposition === "stopped" && currentStage === stageId) return "stopped";
    if ((disposition === "needs_review" || card.needs_review) && currentStage === stageId) return "needs_review";
    if (targetIndex >= 0 && currentIndex >= 0 && targetIndex < currentIndex) return "complete";
    if (stageId === currentStage) return "current";
    return "not_reached";
  }

  function candidateStageLabel(card, stageId) {
    const status = candidateStageStatus(card, stageId);
    if (stageId === "tags") return "Tags";
    if (status === "imported") return "Imported";
    if (status === "not_imported") return "Not imported";
    if (status === "complete") return "Complete";
    if (status === "current") return "Current";
    if (status === "stopped") return "Stopped";
    if (status === "needs_review") return "Needs review";
    return "Not reached";
  }

  function errorMessage(error) {
    if (!error) return "Unknown error";
    const raw = error.message ? String(error.message) : String(error);
    const match = raw.match(/^(\d{3}):\s*(.*)$/s);
    const body = match ? match[2] : raw;
    try {
      const parsed = JSON.parse(body);
      if (typeof parsed.detail === "string") return parsed.detail;
      if (parsed.detail && typeof parsed.detail.message === "string") return parsed.detail.message;
      if (typeof parsed.error === "string") return parsed.error;
    } catch (_err) { /* keep raw message */ }
    return body || raw || "Unknown error";
  }

  function sourceDateQueryValue(value) {
    const raw = String(value || "").trim();
    let match;
    if (!raw) return "";
    match = raw.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
    if (match) return match[1] + "-" + match[2].padStart(2, "0") + "-" + match[3].padStart(2, "0");
    match = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (match) return match[3] + "-" + match[1].padStart(2, "0") + "-" + match[2].padStart(2, "0");
    if (/^\d{1,2}\/\d{0,2}\/?\d{0,3}$/.test(raw)) return "";
    if (/^\d{1,4}-\d{0,2}-?\d{0,2}$/.test(raw)) return "";
    return raw;
  }

  function padDatePart(value) {
    return String(value).padStart(2, "0");
  }

  function utcDateFromParts(year, month, day) {
    const date = new Date(Date.UTC(year, month - 1, day));
    if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
    return date;
  }

  function sourceDateInputSameDay(value) {
    const raw = String(value || "").trim();
    let match = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (match) {
      const month = Number(match[1]);
      const day = Number(match[2]);
      const year = Number(match[3]);
      const date = utcDateFromParts(year, month, day);
      if (!date) return "";
      return padDatePart(date.getUTCMonth() + 1) + "/" + padDatePart(date.getUTCDate()) + "/" + date.getUTCFullYear();
    }
    match = raw.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
    if (match) {
      const year = Number(match[1]);
      const month = Number(match[2]);
      const day = Number(match[3]);
      const date = utcDateFromParts(year, month, day);
      if (!date) return "";
      return date.getUTCFullYear() + "-" + padDatePart(date.getUTCMonth() + 1) + "-" + padDatePart(date.getUTCDate());
    }
    return "";
  }

  function boardURL(sourceType, filter, sort, search, dateFrom, dateTo) {
    return API_BASE + "/board?source_type=" + encodeURIComponent(sourceType || "") +
      "&filter=" + encodeURIComponent(filter || "all") +
      "&sort=" + encodeURIComponent(sort || "newest") +
      "&search=" + encodeURIComponent(search || "") +
      "&date_from=" + encodeURIComponent(sourceDateQueryValue(dateFrom)) +
      "&date_to=" + encodeURIComponent(sourceDateQueryValue(dateTo));
  }

  function detailURL(sourceUnitId, lineageId) {
    return API_BASE + "/source-units/" + encodeURIComponent(sourceUnitId || "") +
      "/thoughts/" + encodeURIComponent(lineageId || "");
  }

  function sourceTypeOptions(sourceTypes) {
    return asArray(sourceTypes).map(function (sourceType) {
      return {
        id: sourceType.id || sourceType.value || "",
        label: sourceType.label || sourceType.id || "Source type",
        count: sourceType.count,
      };
    }).filter(function (sourceType) { return sourceType.id; });
  }

  function ReadOnlyButton(props) {
    return h("button", {
      className: cx("ob-button", "ob-button--readonly", props.className),
      type: "button",
      disabled: true,
      title: READ_ONLY_TITLE,
      "aria-disabled": "true",
    }, props.children);
  }

  function ReadOnlyNote() {
    return h("p", { className: "ob-readonly-note" }, READ_ONLY_EXPLANATION);
  }

  function StatePanel(props) {
    return h("section", {
      className: cx("ob-state", "ob-state--" + (props.tone || "empty")),
      role: props.role || (props.tone === "error" ? "alert" : undefined),
    },
      h("div", { className: "ob-state-title" }, props.title),
      props.message ? h("p", null, props.message) : null,
      props.detail ? h("pre", { className: "ob-state-detail" }, props.detail) : null
    );
  }

  function Header(props) {
    const board = props.board || {};
    const generated = board.generated_at || props.sourceMeta.generated_at;
    const runId = board.ingestion_run_id || props.sourceMeta.ingestion_run_id;
    return h("header", { className: "ob-header" },
      h("div", { className: "ob-title-block" },
        h("div", { className: "ob-kicker" }, "OpenBrain ingestion"),
        h("h1", null, "OpenBrain"),
        h("p", null, "Track source units, current-stage thought cards, review stops, and CortexDB receipts from the latest ingestion snapshot.")
      ),
      h("dl", { className: "ob-snapshot-meta", "aria-label": "Snapshot metadata" },
        h("div", null,
          h("dt", null, "Generated"),
          h("dd", null, formatDate(generated))
        ),
        h("div", null,
          h("dt", null, "Run"),
          h("dd", null, safeText(runId, "sample"))
        )
      )
    );
  }

  function Toolbar(props) {
    const options = sourceTypeOptions(props.sourceTypes);
    return h("section", { className: "ob-toolbar", "aria-label": "OpenBrain board controls" },
      h("label", { className: "ob-field" },
        h("span", null, "Source type"),
        h("select", {
          value: props.sourceType || "",
          onChange: function (event) { props.onSourceTypeChange(event.target.value); },
          "aria-label": "Source type",
        },
          options.length ? options.map(function (sourceType) {
            const suffix = Number.isFinite(Number(sourceType.count)) ? " (" + sourceType.count + ")" : "";
            return h("option", { key: sourceType.id, value: sourceType.id }, sourceType.label + suffix);
          }) : h("option", { value: "" }, "No source types")
        )
      ),
      h("label", { className: "ob-field ob-field--search" },
        h("span", null, "Search"),
        h("input", {
          type: "search",
          value: props.search,
          placeholder: "Search thought text, topics, IDs…",
          onChange: function (event) { props.onSearchChange(event.target.value); },
          "aria-label": "Search thought cards",
        })
      ),
      h("label", { className: "ob-field" },
        h("span", null, "Source date from"),
        h("input", {
          type: "text",
          value: props.dateFrom || "",
          placeholder: "MM/DD/YYYY",
          onChange: function (event) { props.onDateFromChange(event.target.value); },
          "aria-label": "Source date from",
        })
      ),
      h("label", { className: "ob-field" },
        h("span", null, "Source date to"),
        h("input", {
          type: "text",
          value: props.dateTo || "",
          placeholder: "MM/DD/YYYY",
          onChange: function (event) { props.onDateToChange(event.target.value); },
          "aria-label": "Source date to",
        })
      ),
      h("div", { className: "ob-filter-chips", role: "group", "aria-label": "Filter cards" },
        FILTERS.map(function (filter) {
          const pressed = props.filter === filter.value;
          return h("button", {
            key: filter.value,
            type: "button",
            className: cx("ob-chip", pressed && "ob-chip--active"),
            "aria-pressed": pressed,
            onClick: function () { props.onFilterChange(filter.value); },
          }, filter.label);
        })
      ),
      h("label", { className: "ob-field" },
        h("span", null, "Sort"),
        h("select", {
          value: props.sort,
          onChange: function (event) { props.onSortChange(event.target.value); },
          "aria-label": "Sort source units",
        }, SORTS.map(function (sort) {
          return h("option", { key: sort.value, value: sort.value }, sort.label);
        }))
      )
    );
  }

  function Metrics(props) {
    const visible = (props.board && props.board.visible_counts) || {};
    const total = (props.board && props.board.total_counts) || (props.board && props.board.metrics) || {};
    const items = [
      { key: "source_units", label: "Sources" },
      { key: "thoughts", label: "Thoughts" },
      { key: "needs_review", label: "Needs review" },
      { key: "stopped", label: "Stopped" },
      { key: "imported", label: "Imported" },
      { key: "zero_thoughts", label: "Zero thoughts" },
    ];
    return h("section", { className: "ob-metrics", "aria-label": "Board metrics visible/total" },
      h("div", { className: "ob-metric-help" }, "visible/total"),
      items.map(function (item) {
        return h("div", { className: "ob-metric", key: item.key },
          h("span", { className: "ob-metric-label" }, item.label),
          h("span", { className: "ob-metric-value" }, countOf(visible, item.key), " / ", countOf(total, item.key))
        );
      })
    );
  }

  function PolicyDefinitionDialog(props) {
    const definition = props.definition || {};
    if (!definition.label) return null;
    return h("div", { className: "ob-dialog-backdrop ob-policy-definition-backdrop" },
      h("section", {
        className: "ob-policy-definition-dialog",
        role: "dialog",
        "aria-modal": "true",
        "aria-label": "Policy tag definition",
      },
        h("header", { className: "ob-detail-header" },
          h("div", null,
            h("div", { className: "ob-kicker" }, "Policy tag definition"),
            h("h2", null, definition.label)
          ),
          h("button", { type: "button", className: "ob-close", onClick: props.onClose, "aria-label": "Close policy tag definition" }, "×")
        ),
        h("div", { className: "ob-policy-definition-body" },
          h("p", null, definition.definition || "No policy definition supplied for this tag.")
        )
      )
    );
  }

  function BoardView(props) {
    const board = props.board;
    if (props.loading && !board) {
      return h(StatePanel, { tone: "loading", title: "Loading", message: "Loading the OpenBrain ingestion board…" });
    }
    if (props.error && !board) {
      return h(StatePanel, { tone: "error", role: "alert", title: "Unable to load OpenBrain board", message: "The dashboard could not read the ingestion snapshot.", detail: props.error });
    }
    if (!board) {
      return h(StatePanel, { tone: "empty", title: "No board data loaded", message: "Select a source type to load the read-only ingestion board." });
    }

    const columns = asArray(board.columns).length ? asArray(board.columns) : STAGES;
    const rows = asArray(board.rows);
    return h(React.Fragment, null,
      props.error ? h(StatePanel, { tone: "error", role: "alert", title: "Refresh failed", message: "Showing the last loaded board data.", detail: props.error }) : null,
      h(Metrics, { board: board }),
      rows.length === 0 ? h(StatePanel, { tone: "empty", title: "No source units match this filter", message: "Try All, a different source type, broader dates, or a broader search." }) :
        h("section", { className: "ob-board", "aria-label": "OpenBrain ingestion source rows" },
          rows.map(function (row) {
            return h(SourceRow, {
              key: row.id,
              row: row,
              columns: columns,
              expanded: props.expandedRows[row.id] !== false,
              onToggle: props.onToggleRow,
              onOpenDetail: props.onOpenDetail,
              policyDefinitions: policyDefinitions(board),
              onPolicyDefinition: props.onPolicyDefinition,
            });
          })
        )
    );
  }

  function SourceRow(props) {
    const row = props.row || {};
    const rowKey = row.id || row.label || "source";
    const bodyId = "ob-row-body-" + String(rowKey).replace(/[^a-z0-9_-]+/gi, "-");
    const expanded = props.expanded;
    const sourceDate = sourceDateText(row);
    const candidateCards = candidateCardsForRow(row);
    return h("article", { className: "ob-row" },
      h("header", { className: "ob-row-header" },
        h("button", {
          type: "button",
          className: "ob-row-toggle",
          "aria-expanded": expanded,
          "aria-controls": bodyId,
          onClick: function () { props.onToggle(row.id); },
        }, expanded ? "Collapse" : "Expand"),
        h("div", { className: "ob-row-title" },
          h("h2", null, safeText(row.label || row.id, "Source unit")),
          h("p", null, safeText(row.subtitle || row.source_type || row.id, "Source metadata unavailable"))
        ),
        h("div", { className: "ob-row-counts" },
          sourceDate ? h("span", { className: "ob-row-source-date", title: "Source date" }, "Source date: ", sourceDate) : null,
          h("span", null, countOf(row, "thought_count"), " thoughts"),
          countOf(row, "stopped_count") ? h("span", null, countOf(row, "stopped_count"), " stopped") : null
        )
      ),
      expanded ? h("div", { className: "ob-row-body", id: bodyId },
        h(SourceCompactSummary, { row: row }),
        h(EvidenceGrid, {
          row: row,
          cards: evidenceCardsForRow(row),
          onOpenDetail: props.onOpenDetail,
        }),
        countOf(row, "thought_count") === 0 ? h("div", { className: "ob-zero-thoughts" }, "No durable thoughts extracted") :
          h(CandidatePipelineTable, {
            row: row,
            cards: candidateCards,
            onOpenDetail: props.onOpenDetail,
            policyDefinitions: props.policyDefinitions,
            onPolicyDefinition: props.onPolicyDefinition,
          })
      ) : null
    );
  }

  function SourceCompactSummary(props) {
    const row = props.row || {};
    const sourceDate = sourceDateText(row);
    return h("section", { className: "ob-source-summary-strip", "aria-label": "Source summary" },
      h("div", { className: "ob-source-summary-main" },
        h("strong", null, safeText(row.label || row.id, "Source unit")),
        h("span", null, safeText(row.subtitle || row.source_type || row.id, "Source metadata unavailable"))
      ),
      h("div", { className: "ob-source-summary-counts" },
        sourceDate ? h("span", null, "Source date: ", sourceDate) : null,
        h("span", null, countOf(row, "thought_count"), " thoughts"),
        h("span", null, evidenceCardsForRow(row).length, " Evidence"),
        h("span", null, candidateCardsForRow(row).length, " Candidates"),
        countOf(row, "imported_count") ? h("span", null, countOf(row, "imported_count"), " imported") : null,
        countOf(row, "stopped_count") ? h("span", null, countOf(row, "stopped_count"), " stopped") : null
      )
    );
  }

  function EvidenceGrid(props) {
    const cards = asArray(props.cards);
    return h("section", { className: "ob-evidence-section", "aria-label": "Evidence grid" },
      h("div", { className: "ob-section-heading" },
        h("h3", null, "Evidence grid"),
        h("span", { className: "ob-stage-count" }, cards.length)
      ),
      cards.length ? h("div", { className: "ob-evidence-grid", role: "list" }, cards.map(function (card) {
        return h(EvidenceMiniCard, {
          key: card.id || card.lineage_id,
          card: card,
          onOpenDetail: props.onOpenDetail,
        });
      })) : h("div", { className: "ob-stage-empty" }, "No Evidence cards")
    );
  }

  function EvidenceMiniCard(props) {
    const card = props.card || {};
    const evidenceTitle = evidenceCardTitle(card);
    const topics = asArray(card.topics).filter(function (topic) {
      return String(topic || "").trim().toLowerCase() !== "extracted evidence";
    });
    return h("button", {
      type: "button",
      className: cx("ob-evidence-mini-card", "ob-evidence-mini-card--" + stageSlug(card.disposition || "in_progress")),
      onClick: function () { props.onOpenDetail(card, "extracted"); },
      title: evidenceTitle,
      "aria-label": "Open Evidence details for " + evidenceTitle,
      role: "listitem",
    },
      h("strong", { className: "ob-evidence-mini-title" }, evidenceTitle),
      topics.length ? h("span", { className: "ob-topic-list" }, topics.slice(0, 3).map(function (topic, index) {
        return h("span", { key: String(topic) + "-" + index, className: "ob-topic" }, evidenceTopicLabel(topic));
      })) : null
    );
  }

  function CandidatePipelineTable(props) {
    const cards = asArray(props.cards);
    return h("section", { className: "ob-candidate-pipeline", "aria-label": "Candidate memory pipeline" },
      h("div", { className: "ob-section-heading" },
        h("h3", null, "Candidate memory table"),
        h("span", { className: "ob-stage-count" }, cards.length)
      ),
      cards.length ? h("div", { className: "ob-candidate-table-wrap" },
        h("table", { className: "ob-candidate-table" },
          h("thead", null,
            h("tr", null,
              h("th", { scope: "col" }, "Candidate thought"),
              CANDIDATE_TABLE_STAGES.map(function (stage) {
                return h("th", { key: stage.id, scope: "col" }, stage.label);
              })
            )
          ),
          h("tbody", null, cards.map(function (card) {
            return h(CandidateTableRow, {
              key: card.id || card.lineage_id,
              card: card,
              onOpenDetail: props.onOpenDetail,
              policyDefinitions: props.policyDefinitions,
              onPolicyDefinition: props.onPolicyDefinition,
            });
          }))
        )
      ) : h("div", { className: "ob-stage-empty" }, "No Thought candidates")
    );
  }

  function CandidateTableRow(props) {
    const card = props.card || {};
    const effectiveCode = effectiveStopCode(card);
    return h("tr", { className: cx("ob-candidate-row", "ob-candidate-row--" + stageSlug(card.disposition || "in_progress")) },
      h("th", { scope: "row", className: "ob-candidate-title-cell" },
        h("button", {
          type: "button",
          className: "ob-candidate-title-button",
          onClick: function () { props.onOpenDetail(card, "shaped"); },
          "aria-label": "Open Thought candidate details for " + safeText(card.title || card.lineage_id, "candidate"),
        }, safeText(card.title || card.summary || card.lineage_id, "Untitled candidate")),
        card.disposition === "stopped" && effectiveCode ? h("div", { className: "ob-candidate-subline" },
          h(PolicyTag, {
            code: effectiveCode,
            definitions: props.policyDefinitions,
            onShow: props.onPolicyDefinition,
          })
        ) : null
      ),
      CANDIDATE_TABLE_STAGES.map(function (stage) {
        return h(CandidateStageCell, {
          key: stage.id,
          stage: stage,
          card: card,
          onOpenDetail: props.onOpenDetail,
        });
      })
    );
  }

  function CandidateStageCell(props) {
    const card = props.card || {};
    const stage = props.stage || {};
    const status = candidateStageStatus(card, stage.id);
    const label = candidateStageLabel(card, stage.id);
    const ariaLabel = "Open " + safeText(stage.label, "stage") + " details for " + safeText(card.title || card.lineage_id, "candidate");
    if (stage.id === "tags") {
      const topics = asArray(card.topics);
      return h("td", { className: cx("ob-candidate-stage-cell", "ob-candidate-stage-cell--tags") },
        h("button", {
          type: "button",
          className: "ob-candidate-cell-button",
          onClick: function () { props.onOpenDetail(card, "tags"); },
          "aria-label": ariaLabel,
        },
          topics.length ? h("span", { className: "ob-topic-list" }, topics.slice(0, 3).map(function (topic) {
            return h("span", { key: topic, className: "ob-topic" }, topic);
          })) : h("span", { className: "ob-badge" }, "No tags")
        )
      );
    }
    return h("td", { className: cx("ob-candidate-stage-cell", "ob-candidate-stage-cell--" + stageSlug(stage.id), "ob-candidate-stage-cell--" + stageSlug(status)) },
      h("button", {
        type: "button",
        className: "ob-candidate-cell-button",
        onClick: function () { props.onOpenDetail(card, stage.id); },
        "aria-label": ariaLabel,
      },
        h("span", { className: cx("ob-badge", "ob-badge--" + stageSlug(status)) }, label)
      )
    );
  }

  function StageColumn(props) {
    const column = props.column || {};
    const cards = asArray(props.cards);
    const label = columnDisplayLabel(column);
    return h("section", { className: cx("ob-stage", stageClass(column.id)), "aria-label": label },
      h("header", { className: "ob-stage-header" },
        h("span", null, label),
        h("span", { className: "ob-stage-count" }, cards.length)
      ),
      cards.length ? cards.map(function (card) {
        return h(ThoughtCard, {
          key: card.id || card.lineage_id,
          card: card,
          onOpenDetail: props.onOpenDetail,
          policyDefinitions: props.policyDefinitions,
          onPolicyDefinition: props.onPolicyDefinition,
        });
      }) : h("div", { className: "ob-stage-empty" }, "No cards")
    );
  }

  function ThoughtCard(props) {
    const card = props.card || {};
    const disposition = card.disposition || "in_progress";
    const isEvidenceCard = card.current_stage === "extracted";
    const effectiveCode = effectiveStopCode(card);
    const stopCode = disposition === "stopped" ? stopCodeLabel(effectiveCode) : "";
    const stopTitle = stopCodeTitle(effectiveCode);
    const cardSummary = isDuplicateText(card.summary, card.stopped_reason) ? "" : card.summary;
    const detailLabel = "Open details for " + safeText(card.title || card.lineage_id, "thought");
    const cardTopics = asArray(card.topics).filter(function (topic) {
      return !isEvidenceCard || String(topic || "").trim().toLowerCase() !== "extracted evidence";
    });
    const cardProps = { className: cx("ob-thought-card", "ob-thought-card--" + stageSlug(disposition), stageClass(card.current_stage), isEvidenceCard ? "ob-thought-card--clickable" : null) };
    if (isEvidenceCard) {
      cardProps.role = "button";
      cardProps.tabIndex = 0;
      cardProps["aria-label"] = detailLabel;
      cardProps.onClick = function () { props.onOpenDetail(card); };
      cardProps.onKeyDown = function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          props.onOpenDetail(card);
        }
      };
    }
    return h("article", cardProps,
      h("div", { className: "ob-card-head" },
        h("h3", null, safeText(card.title || card.summary || card.lineage_id, "Untitled thought")),
        !isEvidenceCard ? h("div", { className: "ob-card-badges" },
          h("span", { className: cx("ob-badge", "ob-badge--" + stageSlug(disposition)) }, dispositionLabel(disposition)),
          stopCode ? h(PolicyTag, {
            code: effectiveCode,
            definitions: props.policyDefinitions,
            onShow: props.onPolicyDefinition,
          }) : null
        ) : null
      ),
      cardSummary && !isEvidenceCard ? h("p", { className: "ob-card-summary" }, cardSummary) : null,
      cardTopics.length ? h("div", { className: "ob-topic-list" }, cardTopics.map(function (topic) {
        return h("span", { key: topic, className: "ob-topic" }, topic);
      })) : null,
      card.stopped_reason && !isEvidenceCard ? h("p", { className: "ob-card-reason" }, "Stopped: ", card.stopped_reason) : null,
      card.stop_target_label && !isEvidenceCard ? h("p", { className: "ob-card-reason" }, stopTargetText(card)) : null,
      card.cortexdb_id && !isEvidenceCard ? h("p", { className: "ob-card-receipt" }, "CortexDB receipt: ", card.cortexdb_id) : null,
      !isEvidenceCard ? h("button", {
        type: "button",
        className: "ob-detail-link",
        onClick: function () { props.onOpenDetail(card); },
        "aria-label": detailLabel,
      }, "Details →") : null
    );
  }

  function SourceContext(props) {
    const sourceUnit = (props.thought && props.thought.source_unit) || {};
    const sourceRef = sourceUnit.source_ref || {};
    return h("section", { className: "ob-source-context" },
      h("div", { className: "ob-section-heading" },
        h("h3", null, "Source context"),
        h(ReadOnlyButton, null, "Open source")
      ),
      h(ReadOnlyNote, null),
      h("dl", { className: "ob-detail-list" },
        h("div", null, h("dt", null, "Source unit"), h("dd", null, safeText(sourceUnit.id))),
        h("div", null, h("dt", null, "Label"), h("dd", null, safeText(sourceUnit.label))),
        h("div", null, h("dt", null, "Type"), h("dd", null, safeText(sourceUnit.source_type))),
        h("div", null, h("dt", null, "Source date"), h("dd", null, formatSourceDate(sourceUnit.source_date || sourceUnit.occurred_at))),
        h("div", null, h("dt", null, "Processed"), h("dd", null, formatDate(sourceUnit.processed_at))),
        sourceRef.display_path ? h("div", null, h("dt", null, "Display path"), h("dd", null, sourceRef.display_path)) : null
      )
    );
  }

  function statusLabel(status) {
    if (status === "not_reached") return "Not reached";
    if (status === "review_needed") return "Needs review";
    return dispositionLabel(status || "pending");
  }

  function LineageTimeline(props) {
    const stages = asArray(props.thought && props.thought.stages).length ? asArray(props.thought.stages) : STAGES.map(function (stage) {
      return { id: stage.id, label: stage.label, status: stage.id === (props.thought && props.thought.current_stage) ? "current" : "pending" };
    });
    return h("section", { className: "ob-lineage" },
      h("h3", null, "Lineage timeline"),
      h("ol", { className: "ob-lineage-list" }, stages.map(function (stage) {
        return h("li", {
          key: stage.id,
          className: cx("ob-lineage-step", "ob-lineage-step--" + stageSlug(stage.status), "ob-timeline-step--" + stageSlug(stage.id)),
        },
          h("span", { className: "ob-lineage-dot", "aria-hidden": "true" }),
          h("span", { className: "ob-lineage-label" }, stageLabel(stage.id)),
          h("span", { className: "ob-lineage-status" }, statusLabel(stage.status))
        );
      }))
    );
  }

  function traceActorText(actor) {
    if (!actor || typeof actor !== "object") return "";
    return [actor.provider, actor.model, actor.tool || actor.kind].filter(Boolean).join(" / ");
  }

  function traceContextLabel(item) {
    if (!item) return "Other context";
    if (item.label) return item.label;
    const kind = String(item.kind || "other").replace(/_/g, " ");
    return kind.replace(/\b\w/g, function (char) { return char.toUpperCase(); });
  }

  function traceContextValues(item) {
    if (!item || typeof item !== "object") return null;
    const values = item.values || item.metadata || item.fields || item.key_values;
    return values && typeof values === "object" && !Array.isArray(values) ? values : null;
  }

  function TraceKeyValueList(props) {
    const values = props.values || {};
    const entries = Object.keys(values).filter(function (key) { return values[key] !== null && values[key] !== undefined && values[key] !== ""; });
    if (!entries.length) return null;
    return h("dl", { className: "ob-trace-kv-list" }, entries.map(function (key) {
      const value = values[key];
      return h("div", { key: key },
        h("dt", null, key.replace(/_/g, " ")),
        h("dd", null, typeof value === "object" ? JSON.stringify(value) : safeText(value))
      );
    }));
  }

  function traceOutputTitleDistinct(trace, thought) {
    const outputTitle = String((trace && trace.output_title) || "").trim();
    const currentTitle = String((thought && thought.title) || "").trim();
    return outputTitle && outputTitle !== currentTitle;
  }

  function formationTraceHasContent(trace) {
    if (!trace || typeof trace !== "object") return false;
    return Object.keys(trace).some(function (key) {
      const value = trace[key];
      if (key === "version" || key === "stage") return false;
      if (Array.isArray(value)) return value.length > 0;
      if (value && typeof value === "object") return Object.keys(value).length > 0;
      return value !== null && value !== undefined && value !== "";
    });
  }

  function TraceTextBlock(props) {
    const text = props.text ? String(props.text) : "";
    if (!text) return h("p", { className: "ob-muted" }, props.emptyText || "No text recorded.");
    return h("details", { className: "ob-trace-details", open: text.length <= 1500 },
      h("summary", null, props.label),
      props.note ? h("p", { className: "ob-trace-note" }, props.note) : null,
      h("pre", { className: "ob-trace-pre" }, text)
    );
  }

  function FormationLineageCards(props) {
    const cards = asArray(props.trace && props.trace.primary_lineage_cards);
    return h("section", { className: "ob-trace-block ob-trace-lineage" },
      h("h4", null, "Primary lineage cards used"),
      h("p", { className: "ob-muted" }, "These are the Evidence cards directly used to create the Thought candidate."),
      cards.length ? h("ol", { className: "ob-trace-card-list" }, cards.map(function (card, index) {
        const cardId = card.lineage_id || card.id || String(index);
        return h("li", { key: cardId, className: "ob-trace-card" },
          h("div", { className: "ob-trace-card-header" },
            h("code", null, safeText(card.lineage_id || card.id, "Lineage ID unavailable")),
            card.stage ? h("span", { className: cx("ob-badge", stageClass(card.stage)) }, stageLabel(card.stage)) : null
          ),
          card.title ? h("strong", null, safeText(card.title)) : null,
          card.summary ? h("p", null, safeText(card.summary)) : null,
          card.quote || card.source_snippet ? h("blockquote", { className: "ob-source-snippet" }, safeText(card.quote || card.source_snippet)) : null,
          asArray(card.topics).length ? h("div", { className: "ob-topic-list" }, asArray(card.topics).map(function (topic) {
            return h("span", { key: topic, className: "ob-topic" }, topic);
          })) : null
        );
      })) : h("p", { className: "ob-muted" }, "No primary lineage cards recorded.")
    );
  }

  function FormationAdditionalContext(props) {
    const items = asArray(props.trace && props.trace.additional_context_used);
    return h("section", { className: "ob-trace-block ob-trace-context" },
      h("h4", null, "Additional context consulted"),
      h("p", { className: "ob-muted" }, "This is source context used to understand the lineage cards, not separate evidence by itself."),
      items.length ? h("ul", { className: "ob-trace-context-list" }, items.map(function (item, index) {
        const values = traceContextValues(item);
        return h("li", { key: (item.kind || "context") + "-" + index, className: "ob-trace-context-item" },
          h("div", { className: "ob-trace-card-header" },
            h("strong", null, traceContextLabel(item)),
            item.kind ? h("span", { className: "ob-trace-kind" }, item.kind) : null
          ),
          item.lineage_id ? h("code", null, safeText(item.lineage_id)) : null,
          item.title ? h("p", null, h("strong", null, safeText(item.title))) : null,
          item.text ? h("p", null, safeText(item.text)) : null,
          item.summary ? h("p", null, safeText(item.summary)) : null,
          item.quote || item.source_snippet ? h("blockquote", { className: "ob-source-snippet" }, safeText(item.quote || item.source_snippet)) : null,
          h(TraceKeyValueList, { values: values }),
          item.source ? h("small", null, "Source: ", safeText(item.source)) : null,
          item.why_used ? h("small", null, "Why used: ", safeText(item.why_used)) : null
        );
      })) : h("p", { className: "ob-muted" }, "No additional source context recorded.")
    );
  }

  function FormationGateEvents(props) {
    const events = asArray(props.trace && props.trace.gate_events);
    if (!events.length) return null;
    return h("section", { className: "ob-trace-block ob-trace-gates" },
      h("h4", null, "Gate decisions"),
      h("ul", { className: "ob-trace-gate-list" }, events.map(function (event, index) {
        return h("li", { key: (event.stage || "gate") + "-" + index, className: "ob-trace-gate" },
          h("strong", null, stageLabel(event.stage)),
          h("span", null, safeText(event.decision || event.status, "Pending")),
          event.reason ? h("p", null, safeText(event.reason)) : null,
          event.target_label || event.target_id ? h("small", null, safeText(event.target_label || event.target_id)) : null,
          event.created_at ? h("small", null, formatDate(event.created_at)) : null
        );
      }))
    );
  }

  function FormationTrace(props) {
    const thought = props.thought || {};
    const stageTrace = stageDetailFor(thought, "shaped");
    const trace = formationTraceHasContent(stageTrace) ? stageTrace : (thought.formation_trace || null);
    const hasTrace = formationTraceHasContent(trace);
    const primaryIds = hasTrace ? asArray(trace.primary_lineage_ids) : [];
    const contextItems = hasTrace ? asArray(trace.additional_context_used) : [];
    const contextLabels = contextItems.map(traceContextLabel).filter(Boolean).join(", ");
    const actor = hasTrace ? traceActorText(trace.created_by) : "";
    const headingId = "ob-formation-trace-title";
    return h("section", { className: "ob-formation-trace", "aria-labelledby": headingId },
      h("div", { className: "ob-section-heading" },
        h("h3", { id: headingId }, "Formation trace"),
        hasTrace ? h("span", { className: "ob-trace-version" }, trace.version ? "Trace v" + trace.version : "Trace") : null
      ),
      h("p", { className: "ob-trace-note" }, "Shows how this thought candidate was formed from source material. This shows submitted input and output, not hidden model reasoning."),
      !hasTrace ? h("div", { className: "ob-trace-empty" },
        h("p", null, "No formation trace in this snapshot."),
        h("p", { className: "ob-muted" }, "This thought was created before Formation Trace capture was added, or the producer did not provide trace data.")
      ) : h(React.Fragment, null,
        h("dl", { className: "ob-trace-summary ob-detail-list" },
          h("div", null, h("dt", null, "Trace stage"), h("dd", null, stageLabel(trace.stage || thought.current_stage))),
          h("div", null, h("dt", null, "Primary lineage cards"), h("dd", null, primaryIds.length ? primaryIds.length + " — " + primaryIds.join(", ") : "None recorded")),
          h("div", null, h("dt", null, "Extra context"), h("dd", null, contextItems.length ? contextItems.length + " — " + contextLabels : "None recorded")),
          actor ? h("div", null, h("dt", null, "Created by"), h("dd", null, actor)) : null,
          trace.created_at ? h("div", null, h("dt", null, "Created at"), h("dd", null, formatDate(trace.created_at))) : null
        ),
        h(FormationLineageCards, { trace: trace }),
        h(FormationAdditionalContext, { trace: trace }),
        h("section", { className: "ob-trace-block ob-trace-llm-input" },
          h("h4", null, "Exact LLM input sent to shaping step"),
          h(TraceTextBlock, {
            label: "Show submitted input package",
            text: trace.llm_input_text,
            emptyText: "No exact LLM input recorded.",
            note: "This is the submitted input package, not hidden reasoning.",
          })
        ),
        h("section", { className: "ob-trace-block ob-trace-llm-output" },
          h("h4", null, "Output produced by shaping step"),
          traceOutputTitleDistinct(trace, thought) ? h("p", null, h("strong", null, "Shaped title"), ": ", safeText(trace.output_title)) : null,
          h(TraceTextBlock, {
            label: "Show shaped output",
            text: trace.llm_output_text,
            emptyText: "No shaped output recorded.",
          }),
          trace.suggested_type ? h("p", null, h("strong", null, "Suggested type"), ": ", safeText(trace.suggested_type)) : null,
          trace.merge_note ? h("p", null, h("strong", null, "Merge note"), ": ", safeText(trace.merge_note)) : null
        ),
        h(FormationGateEvents, { trace: trace }),
        h("div", { className: "ob-readonly-actions" },
          h(ReadOnlyButton, null, "Copy trace summary"),
          h(ReadOnlyButton, null, "Copy LLM input"),
          h(ReadOnlyButton, null, "Copy shaped output")
        )
      )
    );
  }

  function ImportedReceipt(props) {
    const thought = props.thought || {};
    const receipt = thought.cortexdb_receipt || {};
    return h("section", { className: "ob-imported-receipt ob-detail-variant--imported" },
      h("div", { className: "ob-section-heading" },
        h("h3", null, "CortexDB receipt"),
        h(ReadOnlyButton, null, "Open CortexDB record")
      ),
      h("dl", { className: "ob-detail-list" },
        h("div", null, h("dt", null, "Receipt ID"), h("dd", null, safeText(receipt.id || thought.cortexdb_id))),
        h("div", null, h("dt", null, "Type"), h("dd", null, safeText(receipt.type))),
        h("div", null, h("dt", null, "Captured at"), h("dd", null, formatDate(receipt.captured_at))),
        h("div", null, h("dt", null, "Ingestion run ID"), h("dd", null, safeText(receipt.ingestion_run_id))),
        h("div", null, h("dt", null, "Source unit ID"), h("dd", null, safeText(receipt.source_unit_id))),
        h("div", null, h("dt", null, "Candidate ID"), h("dd", null, safeText(receipt.candidate_id || thought.candidate_id)))
      )
    );
  }

  function StoppedReceipt(props) {
    const thought = props.thought || {};
    const effectiveCode = effectiveStopCode(thought);
    const stopTitle = stopCodeTitle(effectiveCode);
    const stopLabel = stopCodeLabel(effectiveCode);
    return h("section", { className: "ob-stopped-receipt ob-detail-variant--stopped" },
      h("div", { className: "ob-section-heading" },
        h("h3", null, "Stopped / not imported"),
        h(ReadOnlyButton, null, "Copy merge note")
      ),
      h("dl", { className: "ob-detail-list" },
        h("div", null, h("dt", null, "Policy tag"), h("dd", null,
          h(PolicyTag, {
            code: effectiveCode,
            definitions: props.policyDefinitions,
            onShow: props.onPolicyDefinition,
          })
        )),
        thought.stop_target_label ? h("div", null, h("dt", null, "Stop target"), h("dd", null, stopTargetText(thought))) : null,
        thought.stop_stage_id ? h("div", null, h("dt", null, "Stop stage"), h("dd", null, stageLabel(thought.stop_stage_id))) : null,
        h("div", null, h("dt", null, "Stopped reason"), h("dd", null, safeText(thought.stopped_reason, "No stopped reason supplied"))),
        h("div", null, h("dt", null, "Matched memory"), h("dd", null, safeText(thought.matched_memory_id)))
      ),
      h("div", { className: "ob-readonly-actions" },
        h(ReadOnlyButton, null, "Reopen later"),
        h(ReadOnlyButton, null, "Promote later"),
        h(ReadOnlyButton, null, "Mark for review later")
      )
    );
  }

  function RelatedMemories(props) {
    const memories = asArray(props.thought && props.thought.related_memories);
    return h("section", { className: "ob-related" },
      h("div", { className: "ob-section-heading" },
        h("h3", null, "Related memories"),
        memories.length === 0 ? h(ReadOnlyButton, null, "Open matched memory") : null
      ),
      memories.length ? h("ul", { className: "ob-related-list" }, memories.map(function (memory, index) {
        return h("li", { key: memory.id || index, className: "ob-related-memory" },
          h("div", null,
            h("strong", null, safeText(memory.title || memory.id, "Matched memory")),
            h("span", null, safeText(memory.id))
          ),
          memory.score !== undefined && memory.score !== null ? h("span", { className: "ob-score" }, "Score ", String(memory.score)) : null,
          h(ReadOnlyButton, null, "Open matched memory")
        );
      })) : h("p", { className: "ob-muted" }, "No related memories in snapshot.")
    );
  }

  function DetailIDs(props) {
    const thought = props.thought || {};
    return h("dl", { className: "ob-id-strip" },
      h("div", null, h("dt", null, "Lineage ID"), h("dd", null, safeText(thought.lineage_id || thought.id))),
      h("div", null, h("dt", null, "Candidate ID"), h("dd", null, safeText(thought.candidate_id)))
    );
  }

  function DatabaseFieldValue(props) {
    const field = props.field || {};
    const value = field.value;
    if (!field.populated || value === undefined || value === null || value === "") {
      return h("span", { className: "ob-db-empty", "aria-label": "No value in snapshot" }, "—");
    }
    if (value && typeof value === "object") {
      return h("pre", { className: "ob-db-json" }, JSON.stringify(value, null, 2));
    }
    return h("span", { className: "ob-db-value" }, String(value));
  }

  function DatabaseFields(props) {
    const fields = asArray(props.thought && props.thought.database_fields);
    const headingId = "ob-database-fields-title";
    return h("section", { className: "ob-database-fields", "aria-labelledby": headingId },
      h("div", { className: "ob-section-heading" },
        h("h3", { id: headingId }, "Database fields"),
        h("span", { className: "ob-db-table-name" }, "public.thoughts")
      ),
      h("p", { className: "ob-db-note" }, "Actual CortexDB/OpenBrain thought-table columns. A dash means this dashboard snapshot does not have a value for that field."),
      fields.length ? h("dl", { className: "ob-db-field-grid" }, fields.map(function (field) {
        return h("div", {
          key: field.name,
          className: cx("ob-db-field-row", field.populated ? "ob-db-field-row--populated" : "ob-db-field-row--empty"),
        },
          h("dt", { className: "ob-db-field-key" },
            h("code", null, safeText(field.name)),
            field.description ? h("span", null, safeText(field.description)) : null
          ),
          h("dd", { className: "ob-db-field-value" },
            h(DatabaseFieldValue, { field: field }),
            field.note ? h("small", null, safeText(field.note)) : null
          )
        );
      })) : h("p", { className: "ob-muted" }, "No database field metadata in this detail payload.")
    );
  }

  function stageDetailFor(thought, key) {
    const detail = (thought && thought.stage_detail) || {};
    return (detail && detail[key]) || {};
  }

  function stageDetailHasContent(detail, ignoredKeys) {
    if (!detail || typeof detail !== "object") return false;
    const ignored = ignoredKeys || [];
    return Object.keys(detail).some(function (key) {
      if (ignored.indexOf(key) >= 0) return false;
      const value = detail[key];
      if (Array.isArray(value)) return value.length > 0;
      if (value && typeof value === "object") return Object.keys(value).length > 0;
      return value !== null && value !== undefined && value !== "";
    });
  }

  function StageEmpty(props) {
    return h("p", { className: "ob-stage-detail-empty ob-muted" }, props.children);
  }

  function StageKeyValueList(props) {
    const rows = asArray(props.rows).filter(function (row) {
      const value = row && row.value;
      return value !== null && value !== undefined && value !== "" && !(Array.isArray(value) && !value.length);
    });
    if (!rows.length) return null;
    return h("dl", { className: "ob-detail-list ob-stage-detail-summary" }, rows.map(function (row) {
      const value = row.value;
      return h("div", { key: row.label },
        h("dt", null, row.label),
        h("dd", null, Array.isArray(value) ? value.join(", ") : typeof value === "object" ? JSON.stringify(value) : safeText(value))
      );
    }));
  }

  function StageChecklist(props) {
    const items = asArray(props.items);
    if (!items.length) return null;
    return h("ul", { className: "ob-stage-checklist" }, items.map(function (item, index) {
      return h("li", { key: (item.label || "check") + "-" + index, className: "ob-stage-checklist-item" },
        h("strong", null, safeText(item.label || item.name, "Checklist item")),
        h("span", null, safeText(item.status || item.result, "Pending")),
        item.note ? h("p", null, safeText(item.note)) : null
      );
    }));
  }

  function TagsDetailPanel(props) {
    const thought = props.thought || {};
    const topics = asArray(thought.topics);
    const people = asArray(thought.people);
    const detail = stageDetailFor(thought, "tags");
    return h("section", { className: "ob-stage-detail-panel ob-stage-detail-panel--tags" },
      h("div", { className: "ob-section-heading" }, h("h3", null, "Tags")),
      h("p", { className: "ob-stage-detail-note" }, "Candidate metadata used for review and retrieval. Tags are not proof that this candidate was imported into CortexDB."),
      topics.length ? h("div", { className: "ob-topic-list" }, topics.map(function (topic) {
        return h("span", { key: topic, className: "ob-topic" }, topic);
      })) : h(StageEmpty, null, "No tags recorded for this candidate."),
      h(StageKeyValueList, { rows: [
        { label: "People", value: people },
        { label: "Memory type", value: detail.memory_type || thought.memory_type || thought.type },
        { label: "Candidate ID", value: thought.candidate_id },
        { label: "Current stage", value: stageLabel(thought.current_stage) },
      ] })
    );
  }

  function ExtractedEvidencePanel(props) {
    const thought = props.thought || {};
    const detail = stageDetailFor(thought, "extracted");
    const hasDetail = stageDetailHasContent(detail);
    return h("section", { className: "ob-stage-detail-panel ob-stage-detail-panel--extracted" },
      h("div", { className: "ob-section-heading" }, h("h3", null, "Extracted evidence")),
      h("p", { className: "ob-stage-detail-note" }, "What did we pull from the original source, and has it been covered by a Thought candidate?"),
      hasDetail ? h(StageKeyValueList, { rows: [
        { label: "Evidence status", value: detail.status && detail.status.replace(/_/g, " ") },
        { label: "Source section", value: detail.source_section },
        { label: "Extraction method", value: detail.extraction_method },
        { label: "Used by Thought candidates", value: asArray(detail.used_by_shape_ids) },
        { label: "Promotion note", value: detail.promotion_note },
        { label: "Not promoted reason", value: detail.not_promoted_reason },
      ] }) : h(StageEmpty, null, "No extraction coverage data in this snapshot."),
      thought.source_snippet || thought.quote || thought.raw_text ? h("section", { className: "ob-stage-evidence-list" },
        h("h4", null, "Source quote"),
        h("blockquote", { className: "ob-source-snippet" }, thought.source_snippet || thought.quote || thought.raw_text)
      ) : null
    );
  }

  function ShapedFormationPanel(props) {
    return h("section", { className: "ob-stage-detail-panel ob-stage-detail-panel--shaped" },
      h(FormationTrace, { thought: props.thought })
    );
  }

  function PolicyDecisionPanel(props) {
    const thought = props.thought || {};
    const detail = stageDetailFor(thought, "policy");
    const stopped = thought.disposition === "stopped" || detail.result === "stopped";
    const code = detail.stop_code || (stopped ? effectiveStopCode(thought) : "");
    const reason = detail.reason || thought.stopped_reason;
    const hasDetail = stageDetailHasContent(detail, ["result"]) || reason || code;
    return h("section", { className: "ob-stage-detail-panel ob-stage-detail-panel--policy" },
      h("div", { className: "ob-section-heading" }, h("h3", null, "Policy decision")),
      h("p", { className: "ob-stage-detail-note" }, "Is this safe and useful enough to continue?"),
      hasDetail ? h(React.Fragment, null,
        h(StageKeyValueList, { rows: [
          { label: "Policy result", value: detail.result || (thought.disposition === "stopped" ? "stopped" : "not run") },
          { label: stopped ? "Why it stopped" : "Policy reason", value: reason },
          { label: "Redaction note", value: detail.redaction_note },
          { label: "How to fix", value: detail.fix_note },
          { label: "Decided at", value: detail.decided_at && formatDate(detail.decided_at) },
          { label: "Decided by", value: detail.decided_by },
        ] }),
        code ? h("div", { className: "ob-stage-policy-tag" },
          h("span", null, "Policy tag"),
          h(PolicyTag, { code: code, definitions: props.policyDefinitions, onShow: props.onPolicyDefinition })
        ) : null,
        detail.candidate_text_reviewed ? h(TraceTextBlock, { label: "Candidate text reviewed", text: detail.candidate_text_reviewed }) : null,
        detail.redacted_text ? h(TraceTextBlock, { label: "Redacted version", text: detail.redacted_text }) : null
      ) : h(StageEmpty, null, "No policy decision recorded for this card.")
    );
  }

  function DedupeEvidencePanel(props) {
    const thought = props.thought || {};
    const detail = stageDetailFor(thought, "deduped");
    const related = asArray(thought.related_memories)[0] || {};
    const hasDetail = stageDetailHasContent(detail, ["method", "decision"]) || thought.matched_memory_id || related.id;
    return h("section", { className: "ob-stage-detail-panel ob-stage-detail-panel--deduped" },
      h("div", { className: "ob-section-heading" }, h("h3", null, "Dedupe evidence")),
      h("p", { className: "ob-stage-detail-note" }, "Is this new, duplicate, or merged into something else?"),
      hasDetail ? h(React.Fragment, null,
        h(StageKeyValueList, { rows: [
          { label: "Dedupe decision", value: detail.decision },
          { label: "Dedupe method", value: detail.method },
          { label: "Matched memory", value: detail.matched_memory_title || detail.matched_memory_id || thought.matched_memory_id || related.title || related.id },
          { label: "Similarity score", value: detail.similarity_score !== undefined ? detail.similarity_score : related.score },
          { label: "Exact fingerprint", value: detail.content_fingerprint },
          { label: "Merge note", value: detail.merge_note || thought.stopped_reason },
          { label: "Semantic dedupe evidence", value: detail.evidence_note },
        ] })
      ) : h(StageEmpty, null, "No semantic dedupe evidence recorded for this card.")
    );
  }

  function ReadyPackagePanel(props) {
    const thought = props.thought || {};
    const detail = stageDetailFor(thought, "ready_for_cortexdb");
    const finalText = detail.final_memory_text || thought.final_memory_text;
    const topics = asArray(detail.topics && detail.topics.length ? detail.topics : thought.topics);
    const hasDetail = stageDetailHasContent(detail) || finalText;
    return h("section", { className: "ob-stage-detail-panel ob-stage-detail-panel--ready" },
      h("div", { className: "ob-section-heading" },
        h("h3", null, "Ready package"),
        h(ReadOnlyButton, null, "Copy final memory")
      ),
      h("p", { className: "ob-stage-detail-note" }, "Is this final memory package complete enough to import?"),
      hasDetail ? h(React.Fragment, null,
        finalText ? h("section", { className: "ob-final-memory" }, h("h4", null, "Final memory text"), h("p", null, safeText(finalText))) : null,
        h(StageKeyValueList, { rows: [
          { label: "Memory type", value: detail.memory_type },
          { label: "Topics", value: topics },
          { label: "People", value: asArray(detail.people) },
          { label: "Source receipt", value: detail.source_receipt },
          { label: "Policy check", value: detail.policy_check },
          { label: "Dedupe check", value: detail.dedupe_check },
        ] }),
        asArray(detail.checklist).length ? h(React.Fragment, null, h("h4", null, "Import checklist"), h(StageChecklist, { items: detail.checklist })) : null,
        detail.import_payload_preview && Object.keys(detail.import_payload_preview).length ? h("section", { className: "ob-stage-json" },
          h("h4", null, "Import payload preview"),
          h("pre", { className: "ob-db-json" }, JSON.stringify(detail.import_payload_preview, null, 2))
        ) : null
      ) : h(StageEmpty, null, "No ready-to-import package recorded for this card.")
    );
  }

  function CortexDBReceiptPanel(props) {
    const thought = props.thought || {};
    const detail = stageDetailFor(thought, "cortexdb");
    const receipt = Object.keys(detail).length ? detail : (thought.cortexdb_receipt || {});
    const hasReceipt = receipt && Object.keys(receipt).length > 0;
    return h("section", { className: "ob-stage-detail-panel ob-stage-detail-panel--cortexdb" },
      h("div", { className: "ob-section-heading" },
        h("h3", null, "CortexDB receipt"),
        h(ReadOnlyButton, null, "Open CortexDB record")
      ),
      h("p", { className: "ob-stage-detail-note" }, "What actually got stored?"),
      hasReceipt ? h(React.Fragment, null,
        h(StageKeyValueList, { rows: [
          { label: "Thought ID", value: receipt.id || thought.cortexdb_id },
          { label: "Stored text", value: receipt.stored_text || receipt.content || thought.final_memory_text },
          { label: "Type", value: receipt.type },
          { label: "Captured at", value: receipt.captured_at && formatDate(receipt.captured_at) },
          { label: "Ingestion run ID", value: receipt.ingestion_run_id },
          { label: "Source unit ID", value: receipt.source_unit_id },
          { label: "Candidate ID", value: receipt.candidate_id || thought.candidate_id },
          { label: "Update / merge note", value: receipt.update_note },
        ] }),
        receipt.metadata && Object.keys(receipt.metadata).length ? h("section", { className: "ob-stage-json" },
          h("h4", null, "Captured metadata"),
          h("pre", { className: "ob-db-json" }, JSON.stringify(receipt.metadata, null, 2))
        ) : null
      ) : h(StageEmpty, null, "No CortexDB receipt recorded for this card.")
    );
  }

  function StageSpecificDetailPanel(props) {
    const thought = props.thought || {};
    const selectedStage = props.selectedStage || thought.current_stage;
    if (selectedStage === "tags") return h(TagsDetailPanel, props);
    if (selectedStage === "extracted") return h(ExtractedEvidencePanel, props);
    if (selectedStage === "shaped") return h(ShapedFormationPanel, props);
    if (selectedStage === "policy") return h(PolicyDecisionPanel, props);
    if (selectedStage === "deduped") return h(DedupeEvidencePanel, props);
    if (selectedStage === "ready_for_cortexdb") return h(ReadyPackagePanel, props);
    if (selectedStage === "cortexdb") return h(CortexDBReceiptPanel, props);
    return h("section", { className: "ob-stage-detail-panel" },
      h("h3", null, "Stage detail"),
      h("p", { className: "ob-muted" }, "No stage-specific detail is available for this card.")
    );
  }

  function ThoughtDetail(props) {
    const thought = props.thought;
    const dialogTitleId = "ob-detail-title";
    const disposition = thought && thought.disposition ? thought.disposition : "loading";
    const detailSummary = thought && isDuplicateText(thought.summary, thought.stopped_reason) ? "" : thought && thought.summary;
    const detailStopCode = thought ? effectiveStopCode(thought) : "";
    const detailStopLabel = stopCodeLabel(detailStopCode);
    const detailStopTitle = stopCodeTitle(detailStopCode);
    return h("div", { className: "ob-dialog-backdrop" },
      h("section", {
        className: cx("ob-detail", "ob-detail--" + stageSlug(disposition)),
        role: "dialog",
        "aria-modal": "true",
        "aria-labelledby": dialogTitleId,
      },
        h("header", { className: "ob-detail-header" },
          h("div", null,
            h("div", { className: "ob-kicker" }, "Thought detail"),
            h("h2", { id: dialogTitleId }, thought ? safeText(thought.title || thought.summary || thought.lineage_id, "OpenBrain thought") : "Loading")
          ),
          h("button", { type: "button", className: "ob-close", onClick: props.onClose, "aria-label": "Close thought detail" }, "×")
        ),
        props.loading ? h(StatePanel, { tone: "loading", title: "Loading", message: "Loading thought detail…" }) : null,
        props.error ? h(StatePanel, { tone: "error", role: "alert", title: "Unable to load thought detail", message: "The detail endpoint returned an error.", detail: props.error }) : null,
        !props.loading && !props.error && thought ? h("div", { className: "ob-detail-body" },
          h("div", { className: "ob-detail-summary" },
            h("span", { className: cx("ob-badge", "ob-badge--" + stageSlug(thought.disposition)) }, dispositionLabel(thought.disposition)),
            h("span", { className: cx("ob-badge", stageClass(thought.current_stage)) }, stageLabel(thought.current_stage)),
            thought.disposition === "stopped" && detailStopLabel ? h(PolicyTag, {
              code: detailStopCode,
              definitions: props.policyDefinitions,
              onShow: props.onPolicyDefinition,
            }) : null,
            thought.current_stage === "ready_for_cortexdb" ? h("span", { className: "ob-ready-label" }, "Ready for CortexDB") : null
          ),
          h(DetailIDs, { thought: thought }),
          detailSummary ? h("p", { className: "ob-detail-copy" }, detailSummary) : null,
          thought.source_snippet || thought.quote || thought.raw_text ? h("blockquote", { className: "ob-source-snippet" }, thought.source_snippet || thought.quote || thought.raw_text) : null,
          h(StageSpecificDetailPanel, {
            thought: thought,
            selectedStage: props.selectedDetailStage,
            policyDefinitions: props.policyDefinitions,
            onPolicyDefinition: props.onPolicyDefinition,
          }),
          h(SourceContext, { thought: thought }),
          h(LineageTimeline, { thought: thought }),
          h("div", { className: "ob-readonly-actions" },
            h(ReadOnlyButton, null, "Reopen later"),
            h(ReadOnlyButton, null, "Promote later"),
            h(ReadOnlyButton, null, "Mark for review later")
          ),
          h(RelatedMemories, { thought: thought }),
          h(DatabaseFields, { thought: thought })
        ) : null
      )
    );
  }

  function OpenBrainIngestionPage() {
    const sourceTypesState = useState([]);
    const sourceTypes = sourceTypesState[0];
    const setSourceTypes = sourceTypesState[1];
    const sourceMetaState = useState({});
    const sourceMeta = sourceMetaState[0];
    const setSourceMeta = sourceMetaState[1];
    const sourceTypeState = useState("");
    const sourceType = sourceTypeState[0];
    const setSourceType = sourceTypeState[1];
    const filterState = useState("all");
    const filter = filterState[0];
    const setFilter = filterState[1];
    const sortState = useState("newest");
    const sort = sortState[0];
    const setSort = sortState[1];
    const searchState = useState("");
    const search = searchState[0];
    const setSearch = searchState[1];
    const dateFromState = useState("");
    const dateFrom = dateFromState[0];
    const setDateFrom = dateFromState[1];
    const dateToState = useState("");
    const dateTo = dateToState[0];
    const setDateTo = dateToState[1];
    const boardState = useState(null);
    const board = boardState[0];
    const setBoard = boardState[1];
    const loadingState = useState(true);
    const loading = loadingState[0];
    const setLoading = loadingState[1];
    const errorState = useState(null);
    const error = errorState[0];
    const setError = errorState[1];
    const expandedState = useState({});
    const expandedRows = expandedState[0];
    const setExpandedRows = expandedState[1];
    const detailState = useState(null);
    const detail = detailState[0];
    const setDetail = detailState[1];
    const detailLoadingState = useState(false);
    const detailLoading = detailLoadingState[0];
    const setDetailLoading = detailLoadingState[1];
    const detailErrorState = useState(null);
    const detailError = detailErrorState[0];
    const setDetailError = detailErrorState[1];
    const selectedDetailStageState = useState(null);
    const selectedDetailStage = selectedDetailStageState[0];
    const setSelectedDetailStage = selectedDetailStageState[1];
    const policyDefinitionState = useState(null);
    const policyDefinition = policyDefinitionState[0];
    const setPolicyDefinition = policyDefinitionState[1];

    useEffect(function () {
      let alive = true;
      async function loadSourceTypes() {
        try {
          const data = await SDK.fetchJSON(API_BASE + "/source-types");
          if (!alive) return;
          const options = sourceTypeOptions(data && data.source_types);
          setSourceTypes(options);
          setSourceMeta({
            generated_at: data && data.generated_at,
            ingestion_run_id: data && data.ingestion_run_id,
          });
          setSourceType((data && data.default_source_type) || (options[0] && options[0].id) || "transcripts");
          setError(null);
        } catch (err) {
          if (!alive) return;
          setError(errorMessage(err));
          setLoading(false);
        }
      }
      loadSourceTypes();
      return function () { alive = false; };
    }, []);

    useEffect(function () {
      if (!sourceType) return;
      let alive = true;
      async function loadBoard() {
        setLoading(true);
        try {
          const data = await SDK.fetchJSON(boardURL(sourceType, filter, sort, search, dateFrom, dateTo));
          if (!alive) return;
          setBoard(data || null);
          setError(null);
        } catch (err) {
          if (!alive) return;
          setError(errorMessage(err));
        } finally {
          if (alive) setLoading(false);
        }
      }
      loadBoard();
      return function () { alive = false; };
    }, [sourceType, filter, sort, search, dateFrom, dateTo]);

    const selectedSourceTypes = useMemo(function () {
      return sourceTypeOptions(sourceTypes);
    }, [sourceTypes]);

    function toggleRow(rowId) {
      setExpandedRows(function (current) {
        const next = Object.assign({}, current || {});
        next[rowId] = next[rowId] === false;
        return next;
      });
    }

    async function openDetail(card, detailStage) {
      setSelectedDetailStage(detailStage || null);
      setDetail(null);
      setDetailError(null);
      setDetailLoading(true);
      try {
        const data = await SDK.fetchJSON(detailURL(card.source_unit_id, card.lineage_id || card.id));
        setDetail((data && data.thought) || data || null);
      } catch (err) {
        setDetailError(errorMessage(err));
      } finally {
        setDetailLoading(false);
      }
    }

    function closeDetail() {
      setDetail(null);
      setDetailError(null);
      setDetailLoading(false);
      setSelectedDetailStage(null);
    }

    function handleDateFromChange(value) {
      setDateFrom(value);
      setDateTo(function (current) {
        if (String(current || "").trim()) return current;
        return sourceDateInputSameDay(value) || current;
      });
    }

    return h("div", { className: "ob-ingestion" },
      h(Header, { board: board, sourceMeta: sourceMeta }),
      h(Toolbar, {
        sourceTypes: selectedSourceTypes,
        sourceType: sourceType,
        search: search,
        dateFrom: dateFrom,
        dateTo: dateTo,
        filter: filter,
        sort: sort,
        onSourceTypeChange: setSourceType,
        onSearchChange: setSearch,
        onDateFromChange: handleDateFromChange,
        onDateToChange: setDateTo,
        onFilterChange: setFilter,
        onSortChange: setSort,
      }),
      h(BoardView, {
        board: board,
        loading: loading,
        error: error,
        expandedRows: expandedRows,
        onToggleRow: toggleRow,
        onOpenDetail: openDetail,
        onPolicyDefinition: setPolicyDefinition,
      }),
      (detail || detailLoading || detailError) ? h(ThoughtDetail, {
        thought: detail,
        loading: detailLoading,
        error: detailError,
        selectedDetailStage: selectedDetailStage,
        policyDefinitions: board ? policyDefinitions(board) : DEFAULT_POLICY_STOP_DEFINITIONS,
        onPolicyDefinition: setPolicyDefinition,
        onClose: closeDetail,
      }) : null,
      policyDefinition ? h(PolicyDefinitionDialog, {
        definition: policyDefinition,
        onClose: function () { setPolicyDefinition(null); },
      }) : null
    );
  }

  window.__HERMES_PLUGINS__.register("openbrain_ingestion", OpenBrainIngestionPage);
})();
