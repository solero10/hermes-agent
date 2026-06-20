(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;

  const React = SDK.React;
  const h = React.createElement;
  const hooks = SDK.hooks || {};
  const useState = hooks.useState;
  const useEffect = hooks.useEffect;
  const useMemo = hooks.useMemo;
  const C = SDK.components || {};
  const Card = C.Card || "section";
  const CardContent = C.CardContent || "div";

  const API_URL = "/api/plugins/codex_usage_monitor/snapshot?history_points=240";
  const POLL_MS = 30000;
  const ACCOUNT_COLORS = ["#67e8f9", "#a78bfa", "#f59e0b", "#34d399", "#fb7185", "#60a5fa", "#c084fc", "#f472b6"];
  const PACE_COLOR = {
    under: "#22c55e",
    on: "#eab308",
    over: "#ef4444",
    unknown: "#94a3b8",
  };

  function toNumber(value) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
    const number = Number(String(value).replace(/%/g, "").replace(/,/g, ""));
    return Number.isFinite(number) ? number : null;
  }

  function toPercent(value) {
    const number = toNumber(value);
    if (number === null) return null;
    return Math.max(0, Math.min(100, number));
  }

  function formatPercent(value) {
    const percent = toPercent(value);
    if (percent === null) return "—";
    const rounded = Math.round(percent * 10) / 10;
    return (Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)) + "%";
  }

  function parseDate(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatDateTime(value) {
    const date = parseDate(value);
    if (!date) return null;
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatReset(windowData) {
    if (!windowData) return "reset time unavailable";
    if (windowData.reset_at_local) return "resets " + windowData.reset_at_local;
    const formatted = formatDateTime(windowData.reset_at);
    return formatted ? "resets " + formatted : "reset time unavailable";
  }

  function formatAge(seconds) {
    const value = toNumber(seconds);
    if (value === null) return null;
    if (value < 60) return Math.max(0, Math.round(value)) + "s old";
    if (value < 3600) return Math.round(value / 60) + "m old";
    return Math.round(value / 3600) + "h old";
  }

  function errorMessage(error) {
    if (!error) return "Unable to load Codex usage data.";
    const raw = error.message ? String(error.message) : String(error);
    const match = raw.match(/^(\d{3}):\s*(.*)$/s);
    const body = match ? match[2] : raw;
    try {
      const parsed = JSON.parse(body);
      if (typeof parsed.detail === "string") return parsed.detail;
      if (parsed.detail && typeof parsed.detail.message === "string") return parsed.detail.message;
      if (typeof parsed.error === "string") return parsed.error;
    } catch (_err) { /* raw text is already safe to display as text */ }
    return body || raw || "Unable to load Codex usage data.";
  }

  function colorForAccount(account, index) {
    if (account && account.color && /^#[0-9a-f]{3,8}$/i.test(account.color)) return account.color;
    return ACCOUNT_COLORS[index % ACCOUNT_COLORS.length];
  }

  function paceColor(state) {
    const key = state === "under" || state === "on" || state === "over" ? state : "unknown";
    return PACE_COLOR[key];
  }

  function normalizeHistory(history, windowData) {
    const resetDate = parseDate(windowData && windowData.reset_at);
    const periodSeconds = toNumber(windowData && windowData.period_seconds);
    const windowEnd = resetDate ? resetDate.getTime() : null;
    const windowStart = resetDate && periodSeconds && periodSeconds > 0 ? windowEnd - periodSeconds * 1000 : null;
    const rawPoints = Array.isArray(history) ? history : [];
    const points = rawPoints
      .map(function (point) {
        const remaining = point ? toPercent(point.remaining_percent) : null;
        if (remaining === null) return null;
        return {
          remaining: remaining,
          pace: point && point.pace_state ? String(point.pace_state) : "unknown",
          generatedAt: point ? point.generated_at : null,
          synthetic: Boolean(point && point.synthetic),
        };
      })
      .filter(Boolean)
      .filter(function (point) {
        if (windowStart === null || windowEnd === null) return true;
        const generated = parseDate(point.generatedAt);
        if (!generated) return false;
        const timestamp = generated.getTime();
        return timestamp >= windowStart - 60000 && timestamp <= windowEnd + 60000;
      })
      .sort(function (a, b) {
        const aDate = parseDate(a.generatedAt);
        const bDate = parseDate(b.generatedAt);
        const aTime = aDate ? aDate.getTime() : Number.MAX_SAFE_INTEGER;
        const bTime = bDate ? bDate.getTime() : Number.MAX_SAFE_INTEGER;
        return aTime - bTime;
      });

    if (windowStart !== null) {
      const firstDated = points.map(function (point) {
        return parseDate(point.generatedAt);
      }).find(Boolean);
      if (!firstDated || firstDated.getTime() > windowStart + 60000) {
        points.unshift({
          remaining: 100,
          pace: "unknown",
          generatedAt: new Date(windowStart).toISOString(),
          synthetic: true,
        });
      }
    }

    return points;
  }

  function StatusBadge(props) {
    return h("span", { className: "codex-usage-badge codex-usage-badge--" + props.tone }, props.children);
  }

  function StatePanel(props) {
    return h("section", { className: "codex-usage-state codex-usage-state--" + props.tone },
      h("div", { className: "codex-usage-state-title" }, props.title),
      h("p", null, props.message),
      props.detail ? h("pre", { className: "codex-usage-state-detail" }, props.detail) : null
    );
  }

  function SnapshotMeta(props) {
    const snapshot = props.snapshot || {};
    const updated = formatDateTime(snapshot.generated_at);
    const age = formatAge(snapshot.age_seconds);
    const interval = toNumber(snapshot.poll_interval_seconds) || 30;
    const ageSeconds = toNumber(snapshot.age_seconds);
    const isStale = ageSeconds !== null && ageSeconds > interval * 2;

    return h("div", { className: "codex-usage-meta" },
      updated ? h("span", null, "Last updated ", updated) : null,
      age ? h("span", null, age) : null,
      snapshot.cached ? h(StatusBadge, { tone: "cached" }, "cached") : null,
      isStale ? h(StatusBadge, { tone: "stale" }, "stale") : null
    );
  }

  function ChartGrid(layout) {
    const ticks = [100, 75, 50, 25, 0];
    return ticks.map(function (tick) {
      const y = layout.top + ((100 - tick) / 100) * layout.plotHeight;
      return h(React.Fragment, { key: "tick-" + tick },
        h("line", {
          className: "codex-usage-chart-gridline",
          x1: layout.left,
          x2: layout.right,
          y1: y,
          y2: y,
        }),
        h("text", {
          className: "codex-usage-chart-label",
          x: layout.left - 8,
          y: y,
          textAnchor: "end",
          dominantBaseline: "middle",
        }, String(tick))
      );
    });
  }

  function UsageChart(props) {
    const points = normalizeHistory(props.history, props.windowData);
    const width = 320;
    const height = 168;
    const layout = {
      left: 38,
      right: 308,
      top: 14,
      bottom: 144,
      plotHeight: 130,
    };
    const plotWidth = layout.right - layout.left;
    const resetDate = parseDate(props.windowData && props.windowData.reset_at);
    const periodSeconds = toNumber(props.windowData && props.windowData.period_seconds);
    const scaleEnd = resetDate ? resetDate.getTime() : null;
    const scaleStart = resetDate && periodSeconds && periodSeconds > 0 ? scaleEnd - periodSeconds * 1000 : null;

    function xAt(index) {
      if (scaleStart !== null && scaleEnd !== null && scaleEnd > scaleStart) {
        const generated = parseDate(points[index] && points[index].generatedAt);
        if (generated) {
          const ratio = Math.max(0, Math.min(1, (generated.getTime() - scaleStart) / (scaleEnd - scaleStart)));
          return layout.left + plotWidth * ratio;
        }
      }
      if (points.length <= 1) return layout.left + plotWidth / 2;
      return layout.left + (plotWidth * index) / (points.length - 1);
    }

    function yAt(percent) {
      return layout.top + ((100 - percent) / 100) * layout.plotHeight;
    }

    const children = [
      h("rect", { key: "bg", className: "codex-usage-chart-bg", x: layout.left, y: layout.top, width: plotWidth, height: layout.plotHeight }),
      h(ChartGrid, Object.assign({ key: "grid" }, layout)),
      h("line", { key: "axis-y", className: "codex-usage-chart-axis", x1: layout.left, x2: layout.left, y1: layout.top, y2: layout.bottom }),
      h("line", { key: "axis-x", className: "codex-usage-chart-axis", x1: layout.left, x2: layout.right, y1: layout.bottom, y2: layout.bottom }),
    ];

    if (points.length === 0) {
      children.push(h("text", {
        key: "waiting",
        className: "codex-usage-chart-waiting",
        x: layout.left + plotWidth / 2,
        y: layout.top + layout.plotHeight / 2,
        textAnchor: "middle",
        dominantBaseline: "middle",
      }, "waiting for samples"));
    } else {
      for (let i = 1; i < points.length; i += 1) {
        const previous = points[i - 1];
        const current = points[i];
        children.push(h("line", {
          key: "segment-" + i,
          className: "codex-usage-chart-segment",
          x1: xAt(i - 1),
          y1: yAt(previous.remaining),
          x2: xAt(i),
          y2: yAt(current.remaining),
          stroke: paceColor(current.pace || previous.pace),
        }));
      }
      points.forEach(function (point, index) {
        children.push(h("circle", {
          key: "point-" + index,
          className: "codex-usage-chart-point",
          cx: xAt(index),
          cy: yAt(point.remaining),
          r: points.length === 1 ? 3.6 : 2.4,
          fill: paceColor(point.pace),
        }));
      });
    }

    return h("svg", {
      className: "codex-usage-chart",
      viewBox: "0 0 " + width + " " + height,
      role: "img",
      "aria-label": props.label + " remaining usage history",
      preserveAspectRatio: "none",
    }, children);
  }

  function WindowMetric(props) {
    const windowData = props.windowData || {};
    const remaining = formatPercent(windowData.remaining_percent);
    const onPace = toPercent(windowData.on_pace_remaining_percent);
    const onPaceText = onPace === null ? null : " (" + formatPercent(onPace) + " on pace)";
    return h("div", { className: "codex-usage-window" },
      h("div", { className: "codex-usage-window-head" },
        h("span", { className: "codex-usage-window-title" }, props.title),
        h("span", { className: "codex-usage-window-value" }, remaining, onPaceText, " remaining")
      ),
      h("div", { className: "codex-usage-reset" }, formatReset(windowData)),
      h(UsageChart, { history: windowData.history, label: props.title, windowData: windowData })
    );
  }

  function AccountCard(props) {
    const account = props.account || {};
    const windows = account.windows || {};
    const accent = colorForAccount(account, props.index || 0);
    const label = account.label || account.stored_label || account.id || "Codex account";
    const drop = toPercent(account.active_drop_percent);
    const activeText = drop === null ? "recent quota drop" : "recent quota drop · " + formatPercent(drop);

    return h(Card, { className: "codex-usage-card", style: { "--codex-account-accent": accent } },
      h(CardContent, { className: "codex-usage-card-content" },
        h("div", { className: "codex-usage-account-head" },
          h("div", { className: "codex-usage-account-title" },
            h("span", { className: "codex-usage-dot", "aria-hidden": "true" }),
            h("span", null, label)
          ),
          account.active_now ? h("div", { className: "codex-usage-active" }, activeText) : null
        ),
        h("div", { className: "codex-usage-helper" }, "Remaining usage since first sample"),
        account.error ? h("div", { className: "codex-usage-account-error" }, String(account.error)) : null,
        h(WindowMetric, { title: "5-hour", windowData: windows.five_hour }),
        h(WindowMetric, { title: "Weekly", windowData: windows.weekly })
      )
    );
  }

  function CodexUsageMonitor() {
    const state = useState(null);
    const snapshot = state[0];
    const setSnapshot = state[1];
    const loadingState = useState(true);
    const loading = loadingState[0];
    const setLoading = loadingState[1];
    const errorState = useState(null);
    const error = errorState[0];
    const setError = errorState[1];

    useEffect(function () {
      let alive = true;

      async function loadSnapshot() {
        try {
          const data = await SDK.fetchJSON(API_URL);
          if (!alive) return;
          setSnapshot(data || {});
          setError(null);
        } catch (err) {
          if (!alive) return;
          setError(errorMessage(err));
        } finally {
          if (alive) setLoading(false);
        }
      }

      loadSnapshot();
      const timer = window.setInterval(loadSnapshot, POLL_MS);
      return function () {
        alive = false;
        window.clearInterval(timer);
      };
    }, []);

    const accounts = useMemo(function () {
      const rawAccounts = snapshot && Array.isArray(snapshot.accounts) ? snapshot.accounts : [];
      return rawAccounts.slice().sort(function (a, b) {
        const priorityA = toNumber(a && a.priority);
        const priorityB = toNumber(b && b.priority);
        if (priorityA !== null || priorityB !== null) return (priorityA === null ? 9999 : priorityA) - (priorityB === null ? 9999 : priorityB);
        const indexA = toNumber(a && a.index);
        const indexB = toNumber(b && b.index);
        if (indexA !== null || indexB !== null) return (indexA === null ? 9999 : indexA) - (indexB === null ? 9999 : indexB);
        return String((a && a.label) || "").localeCompare(String((b && b.label) || ""));
      });
    }, [snapshot]);

    const source = snapshot && snapshot.source ? snapshot.source : {};
    const noCommand = snapshot && source.available === false;
    const dataError = snapshot && snapshot.ok === false ? (source.last_error || snapshot.error || "Codex usage data is not available yet.") : null;
    const showBlockingError = !snapshot && error;
    const showSetup = noCommand;
    const showEmpty = snapshot && !showSetup && !dataError && accounts.length === 0;

    return h("div", { className: "codex-usage-monitor" },
      h("header", { className: "codex-usage-hero" },
        h("div", null,
          h("div", { className: "codex-usage-kicker" }, "Codex OAuth usage"),
          h("h1", null, "Codex Usage Monitor"),
          h("p", null, "Polls every 30 seconds. Active account is inferred from quota drops, not session mapping.")
        ),
        snapshot ? h(SnapshotMeta, { snapshot: snapshot }) : null
      ),

      loading && !snapshot ? h(StatePanel, { tone: "loading", title: "Loading usage data", message: "Reading the latest Codex quota snapshot…" }) : null,
      showBlockingError ? h(StatePanel, { tone: "error", title: "Unable to load usage", message: "The dashboard could not reach the Codex usage plugin API.", detail: error }) : null,
      showSetup ? h(StatePanel, { tone: "setup", title: "Usage command not found", message: "Install or enable `husage` or `hermes-codex-accounts`, then refresh this dashboard tab.", detail: source.last_error || null }) : null,
      snapshot && dataError && !showSetup ? h(StatePanel, { tone: "error", title: "Usage snapshot unavailable", message: "The usage wrapper returned an error.", detail: dataError }) : null,
      showEmpty ? h(StatePanel, { tone: "empty", title: "No Codex accounts yet", message: "The usage command ran, but it did not return any accounts. Once samples arrive, account tiles and charts will appear here." }) : null,
      snapshot && error && accounts.length > 0 ? h("div", { className: "codex-usage-inline-error" }, "Refresh failed: ", error) : null,

      accounts.length > 0 ? h("section", { className: "codex-usage-grid", "aria-label": "Codex account usage" },
        accounts.map(function (account, index) {
          return h(AccountCard, { key: account.id || account.label || index, account: account, index: index });
        })
      ) : null
    );
  }

  window.__HERMES_PLUGINS__.register("codex_usage_monitor", CodexUsageMonitor);
})();
