"""Compatibility tests for Ken's older auxiliary fallback config keys."""

from __future__ import annotations

from agent import auxiliary_client as aux


def test_model_fallbacks_is_read_as_fallback_chain_when_new_key_absent(monkeypatch):
    config = {
        "auxiliary": {
            "compression": {
                "provider": "custom",
                "model": "primary-model",
                "model_fallbacks": [
                    {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"},
                    {"provider": "custom:local", "model": "qwen", "base_url": "http://127.0.0.1:11434/v1/"},
                ],
            },
        },
    }
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: config)

    task_config = aux._get_auxiliary_task_config("compression")

    assert task_config["fallback_chain"] == [
        {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"},
        {"provider": "custom:local", "model": "qwen", "base_url": "http://127.0.0.1:11434/v1/"},
    ]


def test_fallback_chain_takes_priority_over_legacy_model_fallbacks(monkeypatch):
    config = {
        "auxiliary": {
            "web_extract": {
                "fallback_chain": [
                    {"provider": "anthropic", "model": "claude-sonnet-4"},
                ],
                "model_fallbacks": [
                    {"provider": "openrouter", "model": "should-not-win"},
                ],
            },
        },
    }
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: config)

    task_config = aux._get_auxiliary_task_config("web_extract")

    assert task_config["fallback_chain"] == [
        {"provider": "anthropic", "model": "claude-sonnet-4"},
    ]
