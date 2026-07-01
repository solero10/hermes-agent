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
  const ACCOUNT_QUERY_PARAM = "account";
  const FIVE_HOUR_RANGE_PARAM = "five_hour_range";
  const WEEKLY_RANGE_PARAM = "weekly_range";
  const FIVE_HOUR_FROM_PARAM = "five_hour_from";
  const FIVE_HOUR_TO_PARAM = "five_hour_to";
  const WEEKLY_FROM_PARAM = "weekly_from";
  const WEEKLY_TO_PARAM = "weekly_to";
  const FIVE_HOUR_RANGES = ["1h", "5h", "1d", "7d", "30d"];
  const WEEKLY_RANGES = ["1w", "4w", "12w", "26w", "all"];
  const NEAR_VERTICAL_MIN_DX = 20;
  const NEAR_VERTICAL_MIN_DY = 8;

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

  function dateInputValue(value) {
    const date = parseDate(value);
    if (!date) return "";
    return date.toISOString().slice(0, 10);
  }

  function secondsLeft(windowData) {
    const explicit = toNumber(windowData && windowData.seconds_left);
    if (explicit !== null) return Math.max(0, explicit);
    const resetDate = parseDate(windowData && windowData.reset_at);
    if (!resetDate) return null;
    return Math.max(0, (resetDate.getTime() - Date.now()) / 1000);
  }

  function trimOneDecimal(value) {
    const rounded = Math.round(value * 10) / 10;
    return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  }

  function formatHoursLeft(seconds) {
    const value = toNumber(seconds);
    if (value === null) return null;
    const hours = Math.max(0, value) / 3600;
    return trimOneDecimal(hours) + " " + (Math.round(hours * 10) / 10 === 1 ? "hour" : "hours") + " left";
  }

  function formatDaysTimeLeft(seconds) {
    const value = toNumber(seconds);
    if (value === null) return null;
    const totalMinutes = Math.max(0, Math.round(value / 60));
    const days = Math.floor(totalMinutes / (24 * 60));
    const hours = Math.floor((totalMinutes % (24 * 60)) / 60);
    const minutes = totalMinutes % 60;
    if (days > 0) return days + "d " + hours + "h left";
    if (hours > 0) return hours + "h " + minutes + "m left";
    return minutes + "m left";
  }

  function formatCooldownDuration(seconds) {
    const value = toNumber(seconds);
    if (value === null) return null;
    const totalMinutes = Math.max(0, Math.round(value / 60));
    const days = Math.floor(totalMinutes / (24 * 60));
    const hours = Math.floor((totalMinutes % (24 * 60)) / 60);
    const minutes = totalMinutes % 60;
    if (days > 0) return days + "d " + hours + "h";
    if (hours > 0) return hours + "h " + minutes + "m";
    return minutes + "m";
  }

  function formatReset(windowData, title) {
    if (!windowData) return "reset time unavailable";
    const seconds = secondsLeft(windowData);
    const isWeekly = String(title || windowData.label || windowData.key || "").toLowerCase().includes("week");
    const leftText = isWeekly ? formatDaysTimeLeft(seconds) : formatHoursLeft(seconds);
    if (windowData.reset_at_local) return (leftText ? leftText + " · " : "") + "resets " + windowData.reset_at_local;
    const formatted = formatDateTime(windowData.reset_at);
    return formatted ? (leftText ? leftText + " · " : "") + "resets " + formatted : (leftText || "reset time unavailable");
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

  function formatPlanLabel(planType) {
    const value = String(planType || "").trim().toLowerCase();
    if (!value) return "";

    const normalized = value.replace(/^chatgpt[\s_-]*/, "").replace(/[\s_-]+/g, " ");
    if (/(^|\b)pro(\b|$)/.test(normalized)) return "Pro account";
    if (/(^|\b)plus(\b|$)/.test(normalized)) return "Plus account";
    if (/(^|\b)team(\b|$)/.test(normalized)) return "Team account";
    if (/(^|\b)(enterprise|business)(\b|$)/.test(normalized)) return "Enterprise account";

    const label = normalized.replace(/\b\w/g, function (char) { return char.toUpperCase(); });
    return label ? label + " account" : "";
  }

  function planLabelForAccount(account) {
    const explicitPlan = account && (account.plan_type || account.plan);
    return formatPlanLabel(explicitPlan) || "Plan unknown";
  }

  function resetCreditInfoForAccount(account) {
    const resetCredits = account && account.reset_credits;
    if (!resetCredits || typeof resetCredits !== "object") return null;
    const credits = Array.isArray(resetCredits.credits) ? resetCredits.credits : [];
    let count = toNumber(resetCredits.available_count);
    if (count === null) count = credits.length;
    count = Math.max(0, Math.round(count));
    if (count <= 0) return null;
    const nextCredit = credits[0] || {};
    const expiresText = nextCredit.expires_at_local || formatDateTime(nextCredit.expires_at);
    return {
      count: count,
      label: count + " Codex reset " + (count === 1 ? "credit" : "credits"),
      expiresText: expiresText ? "Earliest expires " + expiresText : "Expiration date unavailable",
    };
  }

  function ResetCredits(props) {
    const info = resetCreditInfoForAccount(props.account);
    if (!info) return null;
    return h("div", { className: "codex-usage-reset-credits" },
      h("div", { className: "codex-usage-reset-credits-count" }, info.label),
      h("div", { className: "codex-usage-reset-credits-expiry" }, info.expiresText)
    );
  }

  function accountRouteId(account, index) {
    const raw = account && (account.id || account.stored_label || account.label || account.display_label);
    const value = raw === null || raw === undefined || raw === "" ? "account-" + index : String(raw);
    return value;
  }

  function selectedAccountIdFromLocation() {
    try {
      const url = new URL(window.location.href);
      return url.searchParams.get(ACCOUNT_QUERY_PARAM) || "";
    } catch (_err) {
      return "";
    }
  }

  function buildAccountDetailUrl(accountId) {
    const url = new URL(window.location.href);
    if (accountId) {
      url.searchParams.set(ACCOUNT_QUERY_PARAM, accountId);
    } else {
      url.searchParams.delete(ACCOUNT_QUERY_PARAM);
    }
    return url.pathname + url.search + url.hash;
  }

  function rangeDefaults() {
    return {
      five_hour: { range: "5h", from: "", to: "" },
      weekly: { range: "4w", from: "", to: "" },
    };
  }

  function rangeConfig(windowKey) {
    if (windowKey === "weekly") {
      return { rangeParam: WEEKLY_RANGE_PARAM, fromParam: WEEKLY_FROM_PARAM, toParam: WEEKLY_TO_PARAM, ranges: WEEKLY_RANGES, fallback: "4w" };
    }
    return { rangeParam: FIVE_HOUR_RANGE_PARAM, fromParam: FIVE_HOUR_FROM_PARAM, toParam: FIVE_HOUR_TO_PARAM, ranges: FIVE_HOUR_RANGES, fallback: "5h" };
  }

  function sanitizeRangeValue(value, config) {
    const raw = String(value || "").trim().toLowerCase();
    if (raw === "custom") return "custom";
    return config.ranges.indexOf(raw) >= 0 ? raw : config.fallback;
  }

  function rangeStateFromLocation() {
    const state = rangeDefaults();
    try {
      const url = new URL(window.location.href);
      ["five_hour", "weekly"].forEach(function (windowKey) {
        const config = rangeConfig(windowKey);
        state[windowKey] = {
          range: sanitizeRangeValue(url.searchParams.get(config.rangeParam), config),
          from: url.searchParams.get(config.fromParam) || "",
          to: url.searchParams.get(config.toParam) || "",
        };
      });
    } catch (_err) { /* defaults are safe */ }
    return state;
  }

  function buildDetailUrl(accountId, rangeState) {
    const url = new URL(window.location.href);
    if (accountId) url.searchParams.set(ACCOUNT_QUERY_PARAM, accountId);
    else url.searchParams.delete(ACCOUNT_QUERY_PARAM);
    const state = rangeState || rangeDefaults();
    ["five_hour", "weekly"].forEach(function (windowKey) {
      const config = rangeConfig(windowKey);
      const value = state[windowKey] || {};
      const range = sanitizeRangeValue(value.range, config);
      if (range === config.fallback) url.searchParams.delete(config.rangeParam);
      else url.searchParams.set(config.rangeParam, range);
      if (value.from) url.searchParams.set(config.fromParam, value.from);
      else url.searchParams.delete(config.fromParam);
      if (value.to) url.searchParams.set(config.toParam, value.to);
      else url.searchParams.delete(config.toParam);
    });
    return url.pathname + url.search + url.hash;
  }

  function rangeLabel(value) {
    const labels = { "1h": "1h", "5h": "5h", "1d": "1d", "7d": "7d", "30d": "30d", "1w": "1w", "4w": "4w", "12w": "12w", "26w": "26w", all: "All", custom: "Custom" };
    return labels[value] || value;
  }

  function rangePayloadForWindow(windowData, windowKey, state) {
    const longHistory = windowData && windowData.long_history && typeof windowData.long_history === "object" ? windowData.long_history : {};
    const config = rangeConfig(windowKey);
    const rangeState = state || { range: config.fallback, from: "", to: "" };
    const range = sanitizeRangeValue(rangeState.range, config);
    const base = range === "custom"
      ? (longHistory.all || longHistory[config.ranges[config.ranges.length - 1]] || longHistory[config.fallback])
      : longHistory[range];
    if (!base || typeof base !== "object") return null;
    const payload = Object.assign({}, base, { points: Array.isArray(base.points) ? base.points.slice() : [], preset: range });
    if (range === "custom") {
      const from = parseDate(rangeState.from);
      const to = parseDate(rangeState.to);
      payload.requested_from = from ? from.toISOString() : base.requested_from;
      payload.requested_to = to ? new Date(to.getTime() + 24 * 60 * 60 * 1000 - 1).toISOString() : base.requested_to;
      payload.custom_range = true;
      if (from && to && from.getTime() > to.getTime()) {
        payload.points = [];
        payload.empty_reason = "invalid_range";
      } else if (from || to) {
        payload.points = payload.points.filter(function (point) {
          const generated = parseDate(point && point.generated_at);
          if (!generated) return false;
          if (from && generated < from) return false;
          if (to && generated > new Date(to.getTime() + 24 * 60 * 60 * 1000 - 1)) return false;
          return true;
        });
        if (!payload.points.length) payload.empty_reason = "outside_retention";
      } else {
        payload.empty_reason = "custom_dates_required";
        payload.points = [];
      }
    }
    return payload;
  }

  function emptyHistoryMessage(reason) {
    if (reason === "invalid_range") return "Choose a start date before the end date.";
    if (reason === "custom_dates_required") return "Choose both dates, then apply the custom range.";
    if (reason === "outside_retention") return "No retained samples for this range.";
    return "waiting for samples";
  }

  function normalizeHistory(history, windowData, options) {
    const longRange = Boolean(options && options.longRange);
    const resetDate = parseDate(windowData && windowData.reset_at);
    const periodSeconds = toNumber(windowData && windowData.period_seconds);
    const windowEnd = resetDate ? resetDate.getTime() : null;
    const windowStart = !longRange && resetDate && periodSeconds && periodSeconds > 0 ? windowEnd - periodSeconds * 1000 : null;
    const rawPoints = Array.isArray(history) ? history : [];
    const points = rawPoints
      .map(function (point) {
        const remaining = point ? toPercent(point.remaining_percent) : null;
        if (remaining === null) return null;
        return {
          remaining: remaining,
          pace: point && point.pace_state ? String(point.pace_state) : "unknown",
          generatedAt: point ? point.generated_at : null,
          resetAt: point ? point.reset_at : null,
          synthetic: Boolean(point && point.synthetic),
        };
      })
      .filter(Boolean)
      .filter(function (point) {
        if (windowStart === null || windowEnd === null) return true;
        const generated = parseDate(point.generatedAt);
        if (!generated) return false;
        const pointReset = parseDate(point.resetAt);
        if (resetDate && pointReset && Math.abs(pointReset.getTime() - resetDate.getTime()) > 60000) return false;
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
    const staleAfter = toNumber(snapshot.stale_after_seconds) || interval * 2;
    const collectorStatus = snapshot.collector_status ? String(snapshot.collector_status) : "";
    const isStale = ageSeconds !== null && ageSeconds > staleAfter;

    return h("div", { className: "codex-usage-meta" },
      updated ? h("span", null, "Last updated ", updated) : null,
      age ? h("span", null, age) : null,
      snapshot.cached ? h(StatusBadge, { tone: "cached" }, "cached") : null,
      collectorStatus ? h(StatusBadge, { tone: collectorStatus === "fresh" ? "cached" : "stale" }, "collector " + collectorStatus) : null,
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

  function visiblePointIndexes(points) {
    const indexes = new Set();
    if (!points.length) return indexes;
    indexes.add(0);
    indexes.add(points.length - 1);
    points.forEach(function (point, index) {
      const previous = points[index - 1];
      const next = points[index + 1];
      if (point.synthetic) indexes.add(index);
      if (previous && point.pace !== previous.pace) {
        indexes.add(index - 1);
        indexes.add(index);
      }
      if (next && point.pace !== next.pace) {
        indexes.add(index);
        indexes.add(index + 1);
      }
      if (previous && next) {
        const before = Math.sign(point.remaining - previous.remaining);
        const after = Math.sign(next.remaining - point.remaining);
        if (before !== 0 && after !== 0 && before !== after) indexes.add(index);
      }
    });
    return Array.from(indexes).sort(function (a, b) { return a - b; });
  }

  function isNearVerticalSegment(previous, current, xForPoint, yForPoint) {
    const dx = Math.abs(xForPoint(current) - xForPoint(previous));
    const dy = Math.abs(yForPoint(current) - yForPoint(previous));
    return dx < NEAR_VERTICAL_MIN_DX && dy > NEAR_VERTICAL_MIN_DY;
  }

  function UsageChart(props) {
    const rangePayload = props.rangePayload || null;
    const points = normalizeHistory(rangePayload ? rangePayload.points : props.history, props.windowData, { longRange: Boolean(rangePayload) });
    const width = 320;
    const height = 168;
    const layout = {
      left: 38,
      right: 308,
      top: 14,
      bottom: 144,
      floorPadding: 8,
    };
    layout.plotBottom = layout.bottom - layout.floorPadding;
    layout.plotHeight = layout.plotBottom - layout.top;
    const plotWidth = layout.right - layout.left;
    const resetDate = parseDate(props.windowData && props.windowData.reset_at);
    const periodSeconds = toNumber(props.windowData && props.windowData.period_seconds);
    const rangeStart = rangePayload ? parseDate(rangePayload.clamped_from || rangePayload.requested_from) : null;
    const rangeEnd = rangePayload ? parseDate(rangePayload.clamped_to || rangePayload.requested_to) : null;
    const scaleEnd = rangeEnd ? rangeEnd.getTime() : (resetDate ? resetDate.getTime() : null);
    const scaleStart = rangeStart ? rangeStart.getTime() : (resetDate && periodSeconds && periodSeconds > 0 ? scaleEnd - periodSeconds * 1000 : null);

    function xForPoint(point) {
      if (scaleStart !== null && scaleEnd !== null && scaleEnd > scaleStart) {
        const generated = parseDate(point && point.generatedAt);
        if (generated) {
          const ratio = Math.max(0, Math.min(1, (generated.getTime() - scaleStart) / (scaleEnd - scaleStart)));
          return layout.left + plotWidth * ratio;
        }
      }
      return null;
    }

    function xAt(index) {
      const exact = xForPoint(points[index]);
      if (exact !== null) return exact;
      if (points.length <= 1) return layout.left + plotWidth / 2;
      return layout.left + (plotWidth * index) / (points.length - 1);
    }

    function yAt(percent) {
      return layout.top + ((100 - percent) / 100) * layout.plotHeight;
    }
    const children = [
      h("rect", { key: "bg", className: "codex-usage-chart-bg", x: layout.left, y: layout.top, width: plotWidth, height: layout.bottom - layout.top }),
      h(ChartGrid, Object.assign({ key: "grid" }, layout)),
      h("line", { key: "axis-y", className: "codex-usage-chart-axis", x1: layout.left, x2: layout.left, y1: layout.top, y2: layout.bottom }),
      h("line", { key: "axis-x", className: "codex-usage-chart-axis", x1: layout.left, x2: layout.right, y1: layout.bottom, y2: layout.bottom }),
    ];

    function xForTimestamp(value) {
      const date = parseDate(value);
      if (!date || scaleStart === null || scaleEnd === null || scaleEnd <= scaleStart) return null;
      const ratio = Math.max(0, Math.min(1, (date.getTime() - scaleStart) / (scaleEnd - scaleStart)));
      return layout.left + plotWidth * ratio;
    }

    if (rangePayload && Array.isArray(rangePayload.reset_markers)) {
      rangePayload.reset_markers.forEach(function (marker, index) {
        const x = xForTimestamp(marker && marker.at);
        if (x === null) return;
        children.push(h("line", {
          key: "reset-marker-" + index,
          className: "codex-usage-reset-marker",
          x1: x,
          x2: x,
          y1: layout.top,
          y2: layout.bottom,
        }));
      });
    }

    if (points.length === 0) {
      children.push(h("text", {
        key: "waiting",
        className: "codex-usage-chart-waiting",
        x: layout.left + plotWidth / 2,
        y: layout.top + layout.plotHeight / 2,
        textAnchor: "middle",
        dominantBaseline: "middle",
      }, emptyHistoryMessage(rangePayload && rangePayload.empty_reason)));
    } else {
      function xForVisiblePoint(point) {
        const exact = xForPoint(point);
        return exact === null ? layout.left + plotWidth / 2 : exact;
      }
      function yForVisiblePoint(point) {
        return yAt(point.remaining);
      }
      for (let i = 1; i < points.length; i += 1) {
        const previous = points[i - 1];
        const current = points[i];
        if (isNearVerticalSegment(previous, current, xForVisiblePoint, yForVisiblePoint)) {
          continue;
        }
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
      visiblePointIndexes(points).filter(function (index) {
        if (index === points.length - 1) return true;
        const point = points[index];
        const previous = points[index - 1];
        const next = points[index + 1];
        if (previous && isNearVerticalSegment(previous, point, xForVisiblePoint, yForVisiblePoint)) return false;
        if (next && isNearVerticalSegment(point, next, xForVisiblePoint, yForVisiblePoint)) return false;
        return true;
      }).forEach(function (index) {
        const point = points[index];
        const isLatest = index === points.length - 1;
        children.push(h("circle", {
          key: "point-" + index,
          className: "codex-usage-chart-point" + (isLatest ? " codex-usage-chart-point--latest" : ""),
          cx: xAt(index),
          cy: yAt(point.remaining),
          r: points.length === 1 || isLatest ? 3.6 : 2.6,
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

  function RangeControls(props) {
    const config = rangeConfig(props.windowKey);
    const state = props.state || { range: config.fallback, from: "", to: "" };
    const selected = sanitizeRangeValue(state.range, config);
    const customId = "codex-usage-custom-" + props.windowKey;
    return h("div", { className: "codex-usage-range-controls", "aria-label": props.title + " history range" },
      h("div", { className: "codex-usage-range-buttons", role: "group", "aria-label": props.title + " presets" },
        config.ranges.map(function (range) {
          return h("button", {
            key: range,
            type: "button",
            className: "codex-usage-range-button" + (selected === range ? " codex-usage-range-button-active" : ""),
            "aria-pressed": selected === range ? "true" : "false",
            onClick: function () { props.onChange(props.windowKey, { range: range }); },
          }, rangeLabel(range));
        }),
        h("button", {
          type: "button",
          className: "codex-usage-range-button" + (selected === "custom" ? " codex-usage-range-button-active" : ""),
          "aria-pressed": selected === "custom" ? "true" : "false",
          onClick: function () { props.onChange(props.windowKey, { range: "custom" }); },
        }, "Custom")
      ),
      selected === "custom" ? h("div", { className: "codex-usage-custom-range", id: customId },
        h("label", null, "From", h("input", {
          type: "date",
          value: dateInputValue(state.from),
          max: dateInputValue(state.to) || undefined,
          onChange: function (event) { props.onChange(props.windowKey, { range: "custom", from: event.target.value }); },
        })),
        h("label", null, "To", h("input", {
          type: "date",
          value: dateInputValue(state.to),
          min: dateInputValue(state.from) || undefined,
          onChange: function (event) { props.onChange(props.windowKey, { range: "custom", to: event.target.value }); },
        }))
      ) : null
    );
  }

  function WindowMetric(props) {
    const windowData = props.windowData || {};
    const rangePayload = props.rangePayload || null;
    const remaining = formatPercent(windowData.remaining_percent);
    const onPace = toPercent(windowData.on_pace_remaining_percent);
    const onPaceText = onPace === null ? null : " (" + formatPercent(onPace) + " on pace)";
    const className = "codex-usage-window" + (props.className ? " " + props.className : "");
    return h("div", { className: className },
      h("div", { className: "codex-usage-window-head" },
        h("span", { className: "codex-usage-window-title" }, props.title),
        h("span", { className: "codex-usage-window-value" }, remaining, onPaceText, " remaining")
      ),
      h("div", { className: "codex-usage-reset" }, formatReset(windowData, props.title)),
      props.rangeState && props.onRangeChange ? h(RangeControls, { title: props.title, windowKey: props.windowKey, state: props.rangeState, onChange: props.onRangeChange }) : null,
      rangePayload ? h("div", { className: "codex-usage-range-meta" }, rangeLabel(rangePayload.preset), " · ", rangePayload.points && rangePayload.points.length ? rangePayload.points.length + " points" : emptyHistoryMessage(rangePayload.empty_reason)) : null,
      h(UsageChart, { history: windowData.history, label: props.title, windowData: windowData, rangePayload: rangePayload })
    );
  }

  function isAccountExhausted(account) {
    if (!account) return false;
    if (account.is_exhausted || account.auth_exhausted || account.window_exhausted) return true;
    const status = String(account.auth_status || account.status || "").toLowerCase();
    if (status === "exhausted" || status === "skipped") return true;
    const windows = account.windows || {};
    return Object.keys(windows).some(function (key) {
      const windowData = windows[key];
      const remaining = toPercent(windowData && windowData.remaining_percent);
      const left = secondsLeft(windowData);
      return remaining !== null && remaining <= 0 && (left === null || left > 0);
    });
  }

  function exhaustedReasonForAccount(account) {
    if (!account) return "Exhausted";
    if (account.exhausted_reason) return String(account.exhausted_reason);
    if (Array.isArray(account.exhausted_windows) && account.exhausted_windows.length > 0) {
      return account.exhausted_windows.map(function (item) { return item && item.label ? item.label : null; }).filter(Boolean).join(" + ") + " exhausted";
    }
    return "Exhausted";
  }

  function cooldownInfoForAccount(account) {
    if (!account) return null;
    let seconds = toNumber(account.cooldown_seconds_left);
    const resetAt = account.cooldown_reset_at || account.auth_exhausted_until || account.exhausted_until;
    const resetLocal = account.cooldown_reset_at_local || account.auth_exhausted_until || account.exhausted_until;
    if (seconds === null) {
      const resetDate = parseDate(resetAt);
      if (resetDate) seconds = Math.max(0, (resetDate.getTime() - Date.now()) / 1000);
    }
    const remainingText = seconds === null ? null : formatCooldownDuration(seconds);
    const resetText = resetLocal || formatDateTime(resetAt);
    if (!remainingText && !resetText) return null;
    return { remainingText: remainingText, resetText: resetText };
  }

  function CooldownNotice(props) {
    const info = cooldownInfoForAccount(props.account);
    if (!info) {
      return h("div", { className: "codex-usage-cooldown" },
        h("span", { className: "codex-usage-cooldown-label" }, "Cooldown timing unavailable")
      );
    }
    return h("div", { className: "codex-usage-cooldown" },
      h("span", { className: "codex-usage-cooldown-label" }, "Cooldown ends in"),
      h("span", { className: "codex-usage-cooldown-value" }, info.remainingText || "—"),
      info.resetText ? h("span", { className: "codex-usage-cooldown-reset" }, "resets " + info.resetText) : null
    );
  }

  function AccountCard(props) {
    const account = props.account || {};
    const windows = account.windows || {};
    const exhausted = isAccountExhausted(account);
    const accent = exhausted ? "#94a3b8" : colorForAccount(account, props.index || 0);
    const label = account.label || account.stored_label || account.id || "Codex account";
    const planLabel = planLabelForAccount(account);
    const drop = toPercent(account.active_drop_percent);
    const activeText = drop === null ? "recent quota drop" : "recent quota drop · " + formatPercent(drop);
    const clickable = typeof props.onOpen === "function";
    const cardClass = "codex-usage-card" +
      (exhausted ? " codex-usage-card-exhausted" : "") +
      (clickable ? " codex-usage-card-clickable" : "");
    const helperText = exhausted ? exhaustedReasonForAccount(account) : "Remaining usage since first sample";

    function activateCard() {
      if (clickable) props.onOpen(account, props.index || 0);
    }

    function handleCardKeyDown(event) {
      if (!clickable) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activateCard();
      }
    }

    return h(Card, {
      className: cardClass,
      "data-exhausted": exhausted ? "true" : "false",
      "data-account-id": accountRouteId(account, props.index || 0),
      role: clickable ? "button" : undefined,
      "aria-label": clickable ? "Open details for " + label : undefined,
      tabIndex: clickable ? 0 : undefined,
      onClick: clickable ? activateCard : undefined,
      onKeyDown: clickable ? handleCardKeyDown : undefined,
      style: { "--codex-account-accent": accent },
    },
      h(CardContent, { className: "codex-usage-card-content" },
        h("div", { className: "codex-usage-account-head" },
          h("div", { className: "codex-usage-account-title" },
            h("span", { className: "codex-usage-dot", "aria-hidden": "true" }),
            h("span", { className: "codex-usage-account-label" }, label),
            h("span", { className: "codex-usage-plan-badge" }, planLabel),
            exhausted ? h("span", { className: "codex-usage-exhausted-badge" }, "Exhausted") : null
          ),
          account.active_now && !exhausted ? h("div", { className: "codex-usage-active" }, activeText) : null
        ),
        h("div", { className: "codex-usage-helper" }, helperText),
        exhausted ? h(CooldownNotice, { account: account }) : null,
        account.error ? h("div", { className: "codex-usage-account-error" }, String(account.error)) : null,
        h(WindowMetric, { title: "5-hour", windowData: windows.five_hour }),
        h(WindowMetric, { title: "Weekly", windowData: windows.weekly }),
        h(ResetCredits, { account: account })
      )
    );
  }

  function AccountDetailPage(props) {
    const account = props.account || {};
    const windows = account.windows || {};
    const accent = colorForAccount(account, props.index || 0);
    const label = account.label || account.stored_label || account.id || "Codex account";
    const planLabel = planLabelForAccount(account);
    const drop = toPercent(account.active_drop_percent);
    const activeText = drop === null ? "recent quota drop" : "recent quota drop · " + formatPercent(drop);

    return h("section", { className: "codex-usage-detail", style: { "--codex-account-accent": accent } },
      h("div", { className: "codex-usage-detail-head" },
        h("button", { type: "button", className: "codex-usage-detail-back", "aria-label": "Back to Codex account list", onClick: props.onBack }, "← Accounts"),
        h("div", { className: "codex-usage-detail-title" },
          h("span", { className: "codex-usage-dot", "aria-hidden": "true" }),
          h("div", null,
            h("div", { className: "codex-usage-detail-name" }, label),
            h("div", { className: "codex-usage-detail-subtitle" }, planLabel, account.active_now ? " · " + activeText : "")
          )
        ),
        h("span", { className: "codex-usage-detail-badge" }, "5-hour + weekly")
      ),
      h("div", { className: "codex-usage-detail-charts" },
        h(WindowMetric, {
          title: "5-hour",
          windowKey: "five_hour",
          windowData: windows.five_hour,
          className: "codex-usage-window-detail",
          rangeState: props.rangeState && props.rangeState.five_hour,
          rangePayload: rangePayloadForWindow(windows.five_hour, "five_hour", props.rangeState && props.rangeState.five_hour),
          onRangeChange: props.onRangeChange,
        }),
        h(WindowMetric, {
          title: "Weekly",
          windowKey: "weekly",
          windowData: windows.weekly,
          className: "codex-usage-window-detail",
          rangeState: props.rangeState && props.rangeState.weekly,
          rangePayload: rangePayloadForWindow(windows.weekly, "weekly", props.rangeState && props.rangeState.weekly),
          onRangeChange: props.onRangeChange,
        })
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
    const selectedAccountState = useState(selectedAccountIdFromLocation);
    const selectedAccountId = selectedAccountState[0];
    const setSelectedAccountId = selectedAccountState[1];
    const rangeStateHook = useState(rangeStateFromLocation);
    const detailRangeState = rangeStateHook[0];
    const setDetailRangeState = rangeStateHook[1];

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

    useEffect(function () {
      function handlePopState() {
        setSelectedAccountId(selectedAccountIdFromLocation());
        setDetailRangeState(rangeStateFromLocation());
      }
      window.addEventListener("popstate", handlePopState);
      return function () {
        window.removeEventListener("popstate", handlePopState);
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

    const selectedAccountEntry = useMemo(function () {
      if (!selectedAccountId) return null;
      for (let index = 0; index < accounts.length; index += 1) {
        const account = accounts[index];
        if (accountRouteId(account, index) === selectedAccountId) return { account: account, index: index };
      }
      return null;
    }, [accounts, selectedAccountId]);

    const source = snapshot && snapshot.source ? snapshot.source : {};
    const noCommand = snapshot && source.available === false;
    const dataError = snapshot && snapshot.ok === false ? (source.last_error || snapshot.error || "Codex usage data is not available yet.") : null;
    const showBlockingError = !snapshot && error;
    const showSetup = noCommand;
    const showEmpty = snapshot && !showSetup && !dataError && accounts.length === 0;

    function openAccountDetail(account, index) {
      const accountId = accountRouteId(account, index);
      window.history.pushState({ codexUsageAccountId: accountId, codexUsageRangeState: detailRangeState }, "", buildDetailUrl(accountId, detailRangeState));
      setSelectedAccountId(accountId);
    }

    function updateDetailRange(windowKey, patch) {
      const next = Object.assign({}, detailRangeState || rangeDefaults());
      const current = Object.assign({}, next[windowKey] || { range: rangeConfig(windowKey).fallback, from: "", to: "" });
      const updated = Object.assign(current, patch || {});
      if (updated.range !== "custom") {
        updated.from = "";
        updated.to = "";
      }
      next[windowKey] = updated;
      setDetailRangeState(next);
      window.history.pushState({ codexUsageAccountId: selectedAccountId, codexUsageRangeState: next }, "", buildDetailUrl(selectedAccountId, next));
    }

    function closeAccountDetail() {
      const state = window.history.state || {};
      if (state.codexUsageAccountId && window.history.length > 1) {
        window.history.back();
        return;
      }
      window.history.replaceState({ codexUsageAccountId: "", codexUsageRangeState: detailRangeState }, "", buildDetailUrl("", detailRangeState));
      setSelectedAccountId("");
    }

    return h("div", { className: "codex-usage-monitor" },
      h("header", { className: "codex-usage-hero" },
        h("div", { className: "codex-usage-hero-title" },
          h("div", { className: "codex-usage-kicker" }, "Codex OAuth usage"),
          h("h1", null, "Codex Usage Monitor")
        ),
        h("p", { className: "codex-usage-poll-note" }, "Polls every 30 seconds. Active account is inferred from quota drops, not session mapping."),
        snapshot ? h(SnapshotMeta, { snapshot: snapshot }) : h("div", { className: "codex-usage-meta", "aria-hidden": "true" })
      ),

      loading && !snapshot ? h(StatePanel, { tone: "loading", title: "Loading usage data", message: "Reading the latest Codex quota snapshot…" }) : null,
      showBlockingError ? h(StatePanel, { tone: "error", title: "Unable to load usage", message: "The dashboard could not reach the Codex usage plugin API.", detail: error }) : null,
      showSetup ? h(StatePanel, { tone: "setup", title: "Usage command not found", message: "Install or enable `husage` or `hermes-codex-accounts`, then refresh this dashboard tab.", detail: source.last_error || null }) : null,
      snapshot && dataError && !showSetup ? h(StatePanel, { tone: "error", title: "Usage snapshot unavailable", message: "The usage wrapper returned an error.", detail: dataError }) : null,
      showEmpty ? h(StatePanel, { tone: "empty", title: "No Codex accounts yet", message: "The usage command ran, but it did not return any accounts. Once samples arrive, account tiles and charts will appear here." }) : null,
      snapshot && error && accounts.length > 0 ? h("div", { className: "codex-usage-inline-error" }, "Refresh failed: ", error) : null,
      snapshot && selectedAccountId && !selectedAccountEntry ? h("div", { className: "codex-usage-inline-error" }, "Selected account was not found. Showing all accounts.") : null,

      selectedAccountEntry ? h(AccountDetailPage, {
        account: selectedAccountEntry.account,
        index: selectedAccountEntry.index,
        rangeState: detailRangeState,
        onRangeChange: updateDetailRange,
        onBack: closeAccountDetail,
      }) : null,

      accounts.length > 0 && !selectedAccountEntry ? h("section", { className: "codex-usage-grid", "aria-label": "Codex account usage" },
        accounts.map(function (account, index) {
          return h(AccountCard, { key: account.id || account.label || index, account: account, index: index, onOpen: openAccountDetail });
        })
      ) : null
    );
  }

  window.__HERMES_PLUGINS__.register("codex_usage_monitor", CodexUsageMonitor);
})();
