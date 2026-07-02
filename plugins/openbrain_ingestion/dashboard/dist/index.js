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
    { id: "extracted", label: "Extracted" },
    { id: "shaped", label: "Shaped" },
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

  function sourceDateInputNextDay(value) {
    const raw = String(value || "").trim();
    let match = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (match) {
      const month = Number(match[1]);
      const day = Number(match[2]);
      const year = Number(match[3]);
      const date = utcDateFromParts(year, month, day);
      if (!date) return "";
      date.setUTCDate(date.getUTCDate() + 1);
      return padDatePart(date.getUTCMonth() + 1) + "/" + padDatePart(date.getUTCDate()) + "/" + date.getUTCFullYear();
    }
    match = raw.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
    if (match) {
      const year = Number(match[1]);
      const month = Number(match[2]);
      const day = Number(match[3]);
      const date = utcDateFromParts(year, month, day);
      if (!date) return "";
      date.setUTCDate(date.getUTCDate() + 1);
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
        countOf(row, "thought_count") === 0 ? h("div", { className: "ob-zero-thoughts" }, "No durable thoughts extracted") :
          h("div", { className: "ob-stage-grid" },
            props.columns.map(function (column) {
              return h(StageColumn, {
                key: column.id,
                column: column,
                cards: asArray(row.columns && row.columns[column.id]),
                onOpenDetail: props.onOpenDetail,
                policyDefinitions: props.policyDefinitions,
                onPolicyDefinition: props.onPolicyDefinition,
              });
            })
          )
      ) : null
    );
  }

  function StageColumn(props) {
    const column = props.column || {};
    const cards = asArray(props.cards);
    return h("section", { className: cx("ob-stage", stageClass(column.id)), "aria-label": stageLabel(column.id) },
      h("header", { className: "ob-stage-header" },
        h("span", null, column.label || stageLabel(column.id)),
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
    const effectiveCode = effectiveStopCode(card);
    const stopCode = disposition === "stopped" ? stopCodeLabel(effectiveCode) : "";
    const stopTitle = stopCodeTitle(effectiveCode);
    const cardSummary = isDuplicateText(card.summary, card.stopped_reason) ? "" : card.summary;
    return h("article", { className: cx("ob-thought-card", "ob-thought-card--" + stageSlug(disposition), stageClass(card.current_stage)) },
      h("div", { className: "ob-card-head" },
        h("h3", null, safeText(card.title || card.summary || card.lineage_id, "Untitled thought")),
        h("div", { className: "ob-card-badges" },
          h("span", { className: cx("ob-badge", "ob-badge--" + stageSlug(disposition)) }, dispositionLabel(disposition)),
          stopCode ? h(PolicyTag, {
            code: effectiveCode,
            definitions: props.policyDefinitions,
            onShow: props.onPolicyDefinition,
          }) : null
        )
      ),
      cardSummary ? h("p", { className: "ob-card-summary" }, cardSummary) : null,
      asArray(card.topics).length ? h("div", { className: "ob-topic-list" }, asArray(card.topics).map(function (topic) {
        return h("span", { key: topic, className: "ob-topic" }, topic);
      })) : null,
      card.stopped_reason ? h("p", { className: "ob-card-reason" }, "Stopped: ", card.stopped_reason) : null,
      card.stop_target_label ? h("p", { className: "ob-card-reason" }, stopTargetText(card)) : null,
      card.cortexdb_id ? h("p", { className: "ob-card-receipt" }, "CortexDB receipt: ", card.cortexdb_id) : null,
      h("button", {
        type: "button",
        className: "ob-detail-link",
        onClick: function () { props.onOpenDetail(card); },
        "aria-label": "Open details for " + safeText(card.title || card.lineage_id, "thought"),
      }, "Details →")
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
          h("span", { className: "ob-lineage-label" }, stage.label || stageLabel(stage.id)),
          h("span", { className: "ob-lineage-status" }, statusLabel(stage.status))
        );
      }))
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
          h(SourceContext, { thought: thought }),
          h(LineageTimeline, { thought: thought }),
          h("section", { className: "ob-final-memory" },
            h("div", { className: "ob-section-heading" },
              h("h3", null, "Final memory"),
              h(ReadOnlyButton, null, "Copy final memory")
            ),
            thought.final_memory_text ? h("p", null, thought.final_memory_text) : h("p", { className: "ob-muted" }, "No final memory text in this snapshot.")
          ),
          thought.disposition === "imported" ? h(ImportedReceipt, { thought: thought }) : null,
          thought.disposition === "stopped" ? h(StoppedReceipt, {
            thought: thought,
            policyDefinitions: props.policyDefinitions,
            onPolicyDefinition: props.onPolicyDefinition,
          }) : null,
          thought.disposition !== "stopped" ? h("div", { className: "ob-readonly-actions" },
            h(ReadOnlyButton, null, "Reopen later"),
            h(ReadOnlyButton, null, "Promote later"),
            h(ReadOnlyButton, null, "Mark for review later")
          ) : null,
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

    async function openDetail(card) {
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
    }

    function handleDateFromChange(value) {
      setDateFrom(value);
      setDateTo(function (current) {
        if (String(current || "").trim()) return current;
        return sourceDateInputNextDay(value) || current;
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
