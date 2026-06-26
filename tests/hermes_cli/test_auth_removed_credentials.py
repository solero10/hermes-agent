from __future__ import annotations

import copy
from contextlib import nullcontext
from pathlib import Path


def _entry(entry_id: str, *, priority: int = 0, account_id: str | None = None) -> dict:
    entry = {
        "id": entry_id,
        "label": entry_id,
        "auth_type": "api_key",
        "priority": priority,
        "source": "manual",
        "access_token": f"fake-token-{entry_id}",
    }
    if account_id:
        entry["tokens"] = {"account_id": account_id}
    return entry


def test_removed_credentials_are_not_returned_from_provider_pool(monkeypatch):
    from hermes_cli import auth

    store = {
        "version": 1,
        "credential_pool": {
            "openai-codex": [
                _entry("kept", priority=0),
                _entry("removed-by-id", priority=1),
                _entry("removed-by-account", priority=2, account_id="acct-deleted"),
            ]
        },
        "credential_pool_removed": {
            "openai-codex": [
                {"id": "removed-by-id"},
                {"account_id": "acct-deleted"},
            ]
        },
    }
    monkeypatch.setattr(auth, "_load_auth_store", lambda auth_file=None: store)
    monkeypatch.setattr(auth, "_load_global_auth_store", lambda: {})

    entries = auth.read_credential_pool("openai-codex")
    assert [entry["id"] for entry in entries] == ["kept"]

    whole_pool = auth.read_credential_pool(None)
    assert [entry["id"] for entry in whole_pool["openai-codex"]] == ["kept"]


def test_removed_global_fallback_credentials_are_not_returned(monkeypatch):
    from hermes_cli import auth

    profile_store = {
        "version": 1,
        "credential_pool": {},
        "credential_pool_removed": {
            "openai-codex": [
                {"account_id": "acct-deleted"},
            ]
        },
    }
    global_store = {
        "version": 1,
        "credential_pool": {
            "openai-codex": [
                _entry("global-removed", priority=0, account_id="acct-deleted"),
                _entry("global-kept", priority=1, account_id="acct-ok"),
            ]
        },
    }
    monkeypatch.setattr(auth, "_load_auth_store", lambda auth_file=None: profile_store)
    monkeypatch.setattr(auth, "_load_global_auth_store", lambda: global_store)

    entries = auth.read_credential_pool("openai-codex")
    assert [entry["id"] for entry in entries] == ["global-kept"]

    whole_pool = auth.read_credential_pool(None)
    assert [entry["id"] for entry in whole_pool["openai-codex"]] == ["global-kept"]


def test_removed_credentials_are_not_selected_by_loaded_pool(monkeypatch):
    from hermes_cli import auth
    from agent import credential_pool

    store = {
        "version": 1,
        "credential_pool": {
            "openai-codex": [
                _entry("kept", priority=0),
                _entry("removed", priority=1),
            ]
        },
        "credential_pool_removed": {
            "openai-codex": [{"id": "removed"}],
        },
    }
    monkeypatch.setattr(auth, "_load_auth_store", lambda auth_file=None: store)
    monkeypatch.setattr(auth, "_load_global_auth_store", lambda: {})
    monkeypatch.setattr(credential_pool, "_seed_from_singletons", lambda provider, entries: (False, set()))
    monkeypatch.setattr(credential_pool, "_seed_from_env", lambda provider, entries: (False, set()))

    pool = credential_pool.load_pool("openai-codex")

    assert [entry.id for entry in pool.entries()] == ["kept"]
    selected = pool.select()
    assert selected is not None
    assert selected.id == "kept"


def test_write_credential_pool_does_not_persist_removed_entries(monkeypatch):
    from hermes_cli import auth

    store = {
        "version": 1,
        "credential_pool_removed": {
            "openai-codex": [{"id": "removed"}],
        },
    }
    saved = {}

    def save_auth_store(next_store):
        saved["store"] = copy.deepcopy(next_store)
        return Path("/tmp/fake-auth.json")

    monkeypatch.setattr(auth, "_auth_store_lock", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(auth, "_load_auth_store", lambda auth_file=None: store)
    monkeypatch.setattr(auth, "_save_auth_store", save_auth_store)

    auth.write_credential_pool(
        "openai-codex",
        [
            _entry("removed", priority=0),
            _entry("kept", priority=1),
        ],
    )

    assert [entry["id"] for entry in saved["store"]["credential_pool"]["openai-codex"]] == ["kept"]
