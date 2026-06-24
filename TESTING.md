# Hermes Agent Testing Guide

This file holds the full testing guidance that used to live in the root
`AGENTS.md`. The root file keeps only the short commands and invariant rules.

## Canonical Runner

**Always prefer `scripts/run_tests.sh`** for repo verification. It activates the
repo virtualenv, runs in a clean environment, and delegates to
`scripts/run_tests_parallel.py`.

```bash
scripts/run_tests.sh                                  # full suite
scripts/run_tests.sh -j 4                             # cap parallel worker count
scripts/run_tests.sh tests/gateway/                   # one directory
scripts/run_tests.sh tests/agent/test_prompt_builder.py  # one file
scripts/run_tests.sh tests/agent/test_prompt_builder.py -- -k TestBuildContextFilesPrompt
scripts/run_tests.sh tests/agent/test_prompt_builder.py -- -q --tb=long
scripts/run_tests.sh -- -v --tb=long                  # pytest args only
```

Important argument rule: anything after a literal `--` is passed through to each
per-file pytest invocation. Positional paths before `--` must be directories or
`.py` files; node IDs such as `tests/foo.py::test_bar` are not discovery paths.
Use a file path plus `-- -k pattern` for a focused test.

## What the Wrapper Enforces

`scripts/run_tests.sh` provides CI-like local behavior:

- Activates `.venv`, `venv`, or `$HOME/.hermes/hermes-agent/venv`.
- Starts from a clean environment with credential variables removed.
- Sets `TZ=UTC`, `LANG=C.UTF-8`, `LC_ALL=C.UTF-8`, and `PYTHONHASHSEED=0`.
- Runs `scripts/run_tests_parallel.py`, which discovers test files and runs one
  `python -m pytest <file>` subprocess per file.
- Avoids persistent xdist workers; per-file subprocesses prevent cross-file
  module-level state leakage.
- Skips integration/e2e/docker directories during default discovery unless the
  user explicitly targets them or uses runner options intended for that path.

Runner knobs:

- `-j N` caps parallel worker count.
- `--file-timeout SECONDS` adjusts the per-file wall-clock cap.
- `--slice I/N` runs one duration-balanced CI-style slice.
- `HERMES_TEST_WORKERS`, `HERMES_TEST_PATHS`, and `HERMES_TEST_SLICE` provide
  environment-based overrides.

## Direct Pytest Use

Use direct pytest only for IDE debugging or when you intentionally need a raw
pytest path. Activate the venv first and remember this does not fully mirror the
wrapper's clean environment.

```bash
source .venv/bin/activate   # or: source venv/bin/activate
python -m pytest tests/agent/test_prompt_builder.py -q
python -m pytest tests/agent/test_prompt_builder.py::TestBuildContextFilesPrompt -q
```

Before opening a PR or claiming a fix is verified, rerun the relevant scope via
`scripts/run_tests.sh`.

## Hermetic Test Rules

- Tests must not write to the real `~/.hermes/` tree. Use `tmp_path`,
  `monkeypatch`, and the existing fixtures that redirect `HERMES_HOME`.
- Tests that mock `Path.home()` for profile features must also set
  `HERMES_HOME`, because runtime code uses `get_hermes_home()`.
- Do not let tests call live gateway/systemd/process-control paths without the
  explicit guard-bypass markers and heavy justification.
- For config migration, provider resolution, plugin discovery, prompt building,
  security boundaries, file/network I/O, and remote backends, prefer real-path
  tests over mocks.

## Don't Write Change-Detector Tests

A test is a **change-detector** if it fails whenever data that is expected to
change gets updated: model catalogs, config-version literals, provider counts,
enumeration counts, skill names, or current catalog snapshots. These tests add
little behavioral coverage and make routine updates noisy.

Do not write:

```python
# catalog snapshot — breaks every model release
assert "gemini-2.5-pro" in _PROVIDER_MODELS["gemini"]
assert "MiniMax-M2.7" in models

# config version literal — breaks every schema bump
assert DEFAULT_CONFIG["_config_version"] == 21

# enumeration count — breaks every time a skill/provider is added
assert len(_PROVIDER_MODELS["huggingface"]) == 8
```

Do write:

```python
# behavior: does the catalog plumbing work at all?
assert "gemini" in _PROVIDER_MODELS
assert len(_PROVIDER_MODELS["gemini"]) >= 1

# behavior: does migration bump the user's version to current latest?
assert raw["_config_version"] == DEFAULT_CONFIG["_config_version"]

# invariant: no plan-only model leaks into the legacy list
assert not (set(moonshot_models) & coding_plan_only_models)

# invariant: every model in the catalog has a context-length entry
for m in _PROVIDER_MODELS["huggingface"]:
    assert m.lower() in DEFAULT_CONTEXT_LENGTHS_LOWER
```

Rule of thumb: if the test reads like a snapshot of current data, rewrite it as
an invariant about how two pieces of data must relate.
