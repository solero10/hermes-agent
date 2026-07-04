import { describe, expect, it } from "vitest";

import { en } from "@/i18n/en";
import { formatBrowserPageTitle, resolvePageTitle } from "./resolve-page-title";

describe("resolvePageTitle", () => {
  it("uses dashboard plugin labels for plugin routes", () => {
    const plugins = [
      { path: "/ops-manager", label: "Ops Manager" },
      { path: "/codex-usage", label: "Codex Usage" },
      { path: "/openbrain", label: "OpenBrain" },
    ];

    expect(resolvePageTitle("/ops-manager", en, plugins)).toBe("Ops Manager");
    expect(resolvePageTitle("/codex-usage", en, plugins)).toBe("Codex Usage");
    expect(resolvePageTitle("/openbrain", en, plugins)).toBe("OpenBrain");
  });

  it("uses built-in navigation labels for built-in routes", () => {
    expect(resolvePageTitle("/sessions", en, [])).toBe("Sessions");
    expect(resolvePageTitle("/cron", en, [])).toBe("Cron");
  });
});

describe("formatBrowserPageTitle", () => {
  it("formats browser tab titles with the Hermes prefix", () => {
    expect(formatBrowserPageTitle("Ops Manager")).toBe("Hermes - Ops Manager");
    expect(formatBrowserPageTitle(" OpenBrain ")).toBe("Hermes - OpenBrain");
  });

  it("falls back to the short brand when no page title is available", () => {
    expect(formatBrowserPageTitle(null)).toBe("Hermes");
    expect(formatBrowserPageTitle("   ")).toBe("Hermes");
  });
});