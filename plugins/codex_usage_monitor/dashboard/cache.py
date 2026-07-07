"""Cache helpers for the Codex Usage dashboard collector.

All runtime files live under the active Hermes profile home.  Helpers in this
module are intentionally small and dependency-light so tests can use a temporary
``HERMES_HOME`` without touching Ken's real profile.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from hermes_constants import get_hermes_home

CACHE_DIRNAME = "codex-usage-monitor"
SCHEMA_VERSION = 1
DASHBOARD_POLL_INTERVAL_SECONDS = 10
COLLECTOR_INTERVAL_SECONDS = 15
STALE_AFTER_SECONDS = 60
RESET_CREDITS_INTERVAL_SECONDS = 30 * 60
LONG_HISTORY_REFRESH_SECONDS = 5 * 60
HISTORY_RECENT_SECONDS = 5 * 60
HISTORY_RECENT_MAX_ROWS = 500
HISTORY_PRUNE_INTERVAL_SECONDS = 60 * 60


class CacheLockTimeout(TimeoutError):
    """Raised when another collector owns the interprocess cache lock."""


def cache_dir() -> Path:
    return get_hermes_home() / "cache" / CACHE_DIRNAME


def latest_path() -> Path:
    return cache_dir() / "latest.json"


def latest_with_history_path() -> Path:
    return cache_dir() / "latest_with_history.json"


def collector_status_path() -> Path:
    return cache_dir() / "collector_status.json"


def reset_credits_path() -> Path:
    return cache_dir() / "reset_credits.json"


def history_maintenance_path() -> Path:
    return cache_dir() / "history_maintenance.json"


def history_path() -> Path:
    return cache_dir() / "history.jsonl"


def lock_path() -> Path:
    return cache_dir() / "collector.lock"


def read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json_atomic(
    path: Path,
    data: dict[str, Any],
    *,
    validator: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Atomically write JSON after optional validation.

    Validation happens before any replace so a bad candidate cannot overwrite a
    previous-good file.  The temporary file is cleaned up on failure.
    """
    if validator is not None:
        validator(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


@contextlib.contextmanager
def collector_lock(timeout_seconds: float = 10.0) -> Iterator[dict[str, Any]]:
    """Acquire the collector interprocess lock with bounded waiting."""
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    handle = path.open("a+", encoding="utf-8")
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError as exc:
                if time.monotonic() - start >= timeout_seconds:
                    raise CacheLockTimeout("Codex usage collector lock is already held") from exc
                time.sleep(0.05)
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "locked_at_monotonic": start}, sort_keys=True))
        handle.flush()
        yield {"lock_path": str(path), "wait_seconds": round(time.monotonic() - start, 3)}
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                handle.seek(0)
                handle.truncate()
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
