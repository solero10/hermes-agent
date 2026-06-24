# Hermes Agent - Development Guide

This is the always-loaded operating card for AI coding assistants working on
this repository. Keep it short: Hermes caps loaded context files, so detailed
reference material lives in linked docs instead of this root file.

**Never give up on the right solution.** Fix the real problem, verify it, and
leave the repo safer than you found it.

## What Hermes Is

Hermes is a personal AI agent that runs the same agent core across a CLI,
messaging gateway, TUI, Electron desktop app, API surface, scheduled jobs, and
subagents. It learns through memory and skills, drives terminal/browser tools,
and is extended primarily through plugins, skills, CLI commands, and MCP — not
by expanding the core model-tool surface.

Two design invariants shape almost every change:

- **Prompt caching is sacred.** Do not mutate past context, change toolsets, or
  rebuild system prompts mid-conversation except through the explicit context
  compression path. Cache-breaking changes multiply user cost.
- **The core is a narrow waist.** Every core model tool is sent on every API
  call. Prefer existing code, CLI commands, skills, service-gated tools,
  plugins, or MCP before adding core tools.

## Daily Workflow

1. **Gather context before editing.** Read relevant files with `read_file`, find
   code with `search_files`, and trace symbols to definitions/usages. Do not
   invent files, APIs, imports, or project structure.
2. **Make bounded changes.** Touch only what the task needs. Match existing
   style. Use `patch` for edits and `write_file` for new files. Do not paste
   code in chat instead of changing files.
3. **Protect user state and secrets.** Never read, print, or commit secrets
   unless explicitly asked. Do not modify other Hermes profiles' skills,
   plugins, cron jobs, or memories unless explicitly directed.
4. **Verify with real commands.** Run the targeted tests/lints/builds that prove
   the change. If a command fails, diagnose the real blocker instead of claiming
   success.
5. **Report evidence.** Summaries should name changed files, commands run, and
   any remaining risks or follow-ups.

## Setup and Test Commands

```bash
# Prefer .venv; fall back to venv if that is what the checkout has.
source .venv/bin/activate   # or: source venv/bin/activate

# Always prefer the wrapper. It matches CI better than direct pytest.
scripts/run_tests.sh                                  # full suite
scripts/run_tests.sh tests/gateway/                   # focused directory
scripts/run_tests.sh tests/agent/test_foo.py          # focused file
scripts/run_tests.sh tests/agent/test_foo.py -- -k test_x  # focused test/filter
scripts/run_tests.sh tests/agent/test_foo.py -- -q --tb=long  # pytest flags after --
scripts/run_tests.sh -j 4 tests/agent/                # cap parallelism
```

Testing rules:

- Use `scripts/run_tests.sh`, not direct `pytest`, unless a narrow debugging
  exception is justified.
- Tests must not write to the real `~/.hermes/`; the suite redirects
  `HERMES_HOME` to temp dirs.
- Write behavior/invariant tests, not change-detectors. Do not freeze current
  model names, config-version literals, provider counts, or catalog snapshots.
- For config, routing, security, file/network I/O, providers, and prompt/schema
  behavior, prefer real-path tests over mocks.

Full testing details: [`TESTING.md`](TESTING.md).

## Where Things Live

File counts and module boundaries shift; use the filesystem as canonical. These
are the usual entry points:

```text
run_agent.py                  AIAgent conversation loop
agent/prompt_builder.py       system prompt, context files, skills index
agent/system_prompt.py        prompt assembly tiers and cache-sensitive pieces
model_tools.py                tool discovery/dispatch integration
toolsets.py                   toolset definitions and default tool bundles
tools/                        individual tool implementations + registry
cli.py                        classic interactive CLI orchestration
hermes_cli/                   CLI subcommands, config, setup, plugins, web server
gateway/                      messaging gateway and platform adapters
tui_gateway/                  Python JSON-RPC backend for Ink TUI / desktop
ui-tui/                       Ink/React terminal UI
apps/desktop/                 Electron desktop chat app
plugins/                      bundled plugin surfaces and plugin implementations
skills/, optional-skills/     bundled and optional skills
cron/                         scheduled job store and scheduler
tests/                        pytest suite
website/                      Docusaurus docs
```

Detailed architecture notes: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Contribution and Design Rules

Use the smallest permanent surface that solves the problem:

1. Extend existing code.
2. Add a CLI command + skill.
3. Add a service-gated tool with `check_fn`.
4. Build a plugin.
5. Add an MCP server/catalog entry.
6. Add a new core tool only as a last resort.

Other high-value rules:

- Fix real bugs with a reproducible symptom and line-level cause.
- Preserve message role alternation and prompt-cache stability.
- Keep non-secret settings in `config.yaml`; `.env` is for API keys, tokens,
  passwords, and other secrets only.
- New dependencies need bounded versions; security-sensitive pins need the
  rules in `CONTRIBUTING.md`.
- Plugins must not special-case themselves by modifying core files. Widen a
  generic plugin surface instead.
- For profile-aware state paths, use `get_hermes_home()`; for user-facing
  path text, use `display_hermes_home()`.

Full contribution/review policy: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Profile-Safe Paths

Hermes supports isolated profiles. Code that hardcodes `~/.hermes` or
`Path.home() / ".hermes"` for runtime state is usually wrong.

```python
# GOOD
from hermes_constants import get_hermes_home, display_hermes_home
config_path = get_hermes_home() / "config.yaml"
print(f"Config saved to {display_hermes_home()}/config.yaml")

# BAD for profile-aware runtime state
config_path = Path.home() / ".hermes" / "config.yaml"
print("Config saved to ~/.hermes/config.yaml")
```

Profile operations themselves are HOME-anchored by design so any active profile
can list/manage all profiles. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the
full profile section.

## Top Pitfalls

- Do not hardcode `~/.hermes` paths in runtime code or tests.
- Do not introduce new `simple_term_menu` usage; use the curses UI helpers.
- Do not use `\033[K` in spinner/display code; it leaks under prompt_toolkit.
- Do not hardcode cross-tool references in schema descriptions; toolsets vary.
- Gateway control/approval commands must bypass both message guards.
- Do not wire dead code into live paths without E2E validation.
- Before squash-merging stale branches, rebase/reset to current `main` and
  inspect the resulting diff for accidental reverts.

Full pitfall notes: [`PITFALLS.md`](PITFALLS.md).

## Reference Map

- [`CONTRIBUTING.md`](CONTRIBUTING.md): review rubric, Footprint Ladder,
  dependency/config/tool contribution rules, prompt-cache policy.
- [`ARCHITECTURE.md`](ARCHITECTURE.md): project layout, agent loop, CLI, TUI,
  desktop, tools, plugins, skills, cron, kanban, profiles.
- [`TESTING.md`](TESTING.md): test wrapper, subprocess isolation, CI-parity
  reasons, change-detector examples.
- [`PITFALLS.md`](PITFALLS.md): historical hazards and sharp edges.

This first-pass split is docs-only: Hermes currently loads only the root
`AGENTS.md` from the working directory. If you add subsystem-specific docs later,
keep pointers here or update the loader deliberately with tests.
