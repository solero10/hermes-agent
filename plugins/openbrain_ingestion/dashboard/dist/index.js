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
    { id: "deduped", label: "Deduped" },
    { id: "policy", label: "Policy" },
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

  function stopCodeLabel(value) {
    const slug = String(value || "").replace(/\s+/g, "_").replace(/-/g, "_").toLowerCase();
    if (!slug) return "";
    if (slug === "duplicate") return "Duplicate";
    if (slug === "reference_merge" || slug === "reference/merge") return "Reference / merge";
    if (slug === "policy") return "Policy stop";
    if (slug === "obsolete") return "Obsolete";
    if (slug === "non_thought") return "Not a thought";
    if (slug === "needs_review") return "Needs review";
    if (slug === "other") return "Stopped";
    return slug.replace(/_/g, " ").replace(/\b\w/g, function (char) { return char.toUpperCase(); });
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

  function boardURL(sourceType, filter, sort, search) {
    return API_BASE + "/board?source_type=" + encodeURIComponent(sourceType || "") +
      "&filter=" + encodeURIComponent(filter || "all") +
      "&sort=" + encodeURIComponent(sort || "newest") +
      "&search=" + encodeURIComponent(search || "");
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
      rows.length === 0 ? h(StatePanel, { tone: "empty", title: "No source units match this filter", message: "Try All, a different source type, or a broader search." }) :
        h("section", { className: "ob-board", "aria-label": "OpenBrain ingestion source rows" },
          rows.map(function (row) {
            return h(SourceRow, {
              key: row.id,
              row: row,
              columns: columns,
              expanded: props.expandedRows[row.id] !== false,
              onToggle: props.onToggleRow,
              onOpenDetail: props.onOpenDetail,
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
        return h(ThoughtCard, { key: card.id || card.lineage_id, card: card, onOpenDetail: props.onOpenDetail });
      }) : h("div", { className: "ob-stage-empty" }, "No cards")
    );
  }

  function ThoughtCard(props) {
    const card = props.card || {};
    const disposition = card.disposition || "in_progress";
    const stopCode = disposition === "stopped" ? stopCodeLabel(card.stop_code) : "";
    return h("article", { className: cx("ob-thought-card", "ob-thought-card--" + stageSlug(disposition), stageClass(card.current_stage)) },
      h("div", { className: "ob-card-head" },
        h("h3", null, safeText(card.title || card.summary || card.lineage_id, "Untitled thought")),
        h("div", { className: "ob-card-badges" },
          h("span", { className: cx("ob-badge", "ob-badge--" + stageSlug(disposition)) }, dispositionLabel(disposition)),
          stopCode ? h("span", { className: cx("ob-badge", "ob-badge--stopped") }, stopCode) : null
        )
      ),
      card.summary ? h("p", { className: "ob-card-summary" }, card.summary) : null,
      h("dl", { className: "ob-card-meta" },
        h("div", null, h("dt", null, "Stage"), h("dd", null, stageLabel(card.current_stage))),
        h("div", null, h("dt", null, "Lineage"), h("dd", null, safeText(card.lineage_id || card.id))),
        card.candidate_id ? h("div", null, h("dt", null, "Candidate"), h("dd", null, card.candidate_id)) : null
      ),
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
        h("div", null, h("dt", null, "Occurred"), h("dd", null, formatDate(sourceUnit.occurred_at))),
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
    return h("section", { className: "ob-stopped-receipt ob-detail-variant--stopped" },
      h("div", { className: "ob-section-heading" },
        h("h3", null, "Stopped / not imported"),
        h(ReadOnlyButton, null, "Copy merge note")
      ),
      h("dl", { className: "ob-detail-list" },
        h("div", null, h("dt", null, "Stop code"), h("dd", null, safeText(stopCodeLabel(thought.stop_code), "Stopped"))),
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

  function ThoughtDetail(props) {
    const thought = props.thought;
    const dialogTitleId = "ob-detail-title";
    const disposition = thought && thought.disposition ? thought.disposition : "loading";
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
            thought.disposition === "stopped" && stopCodeLabel(thought.stop_code) ? h("span", { className: cx("ob-badge", "ob-badge--stopped") }, stopCodeLabel(thought.stop_code)) : null,
            h("span", { className: "ob-ready-label" }, "Ready for CortexDB")
          ),
          h(DetailIDs, { thought: thought }),
          thought.summary ? h("p", { className: "ob-detail-copy" }, thought.summary) : null,
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
          thought.disposition === "stopped" ? h(StoppedReceipt, { thought: thought }) : null,
          thought.disposition !== "stopped" ? h("div", { className: "ob-readonly-actions" },
            h(ReadOnlyButton, null, "Reopen later"),
            h(ReadOnlyButton, null, "Promote later"),
            h(ReadOnlyButton, null, "Mark for review later")
          ) : null,
          h(RelatedMemories, { thought: thought })
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
          const data = await SDK.fetchJSON(boardURL(sourceType, filter, sort, search));
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
    }, [sourceType, filter, sort, search]);

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

    return h("div", { className: "ob-ingestion" },
      h(Header, { board: board, sourceMeta: sourceMeta }),
      h(Toolbar, {
        sourceTypes: selectedSourceTypes,
        sourceType: sourceType,
        search: search,
        filter: filter,
        sort: sort,
        onSourceTypeChange: setSourceType,
        onSearchChange: setSearch,
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
      }),
      (detail || detailLoading || detailError) ? h(ThoughtDetail, {
        thought: detail,
        loading: detailLoading,
        error: detailError,
        onClose: closeDetail,
      }) : null
    );
  }

  window.__HERMES_PLUGINS__.register("openbrain_ingestion", OpenBrainIngestionPage);
})();
