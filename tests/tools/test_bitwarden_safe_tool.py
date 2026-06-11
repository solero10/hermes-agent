from __future__ import annotations

import json
import subprocess

import pytest

import tools.bitwarden_safe_tool as bws


FORBIDDEN_SECRET_KEYS = {
    "password",
    "secret_value",
    "token_value",
    "raw_secret",
    "totp",
    "cookie",
    "session_key",
    "recovery_code",
    "item_id",
    "bitwarden_item_id",
}


class _FakeCompletedProcess:
    def __init__(self, args, returncode=0, stdout="", stderr=""):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _json_cp(cmd, payload, returncode=0):
    return _FakeCompletedProcess(cmd, returncode=returncode, stdout=json.dumps(payload), stderr="")


def _load_result(raw: str) -> dict:
    result = json.loads(raw)
    assert isinstance(result, dict)
    assert result.get("secret_exposed_to_llm") is False
    assert_no_forbidden_secret_keys(result)
    return result


def assert_no_forbidden_secret_keys(value, path=()):
    if isinstance(value, dict):
        for key, nested in value.items():
            assert key.lower() not in FORBIDDEN_SECRET_KEYS, ".".join(path + (key,))
            assert_no_forbidden_secret_keys(nested, path + (key,))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            assert_no_forbidden_secret_keys(nested, path + (str(index),))


def test_invalid_domain_preflight_failure_does_not_call_capture_transient_secret(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        assert cmd[1] == "preflight-update-password"
        return _json_cp(
            cmd,
            {
                "success": False,
                "error": "domain is not allowed for alias",
                "password": "redacted by sanitizer",
            },
            returncode=1,
        )

    def fail_capture(*args, **kwargs):  # pragma: no cover - should never execute
        raise AssertionError("capture_transient_secret should not be called")

    import tools.skills_tool as skills_tool

    monkeypatch.setattr(bws.subprocess, "run", fake_run)
    monkeypatch.setattr(skills_tool, "capture_transient_secret", fail_capture)

    result = _load_result(bws.bitwarden_set_website_password("openrouter", "bad.example"))

    assert result["success"] is False
    assert "domain" in result["error"]
    assert [call["cmd"][1] for call in calls] == ["preflight-update-password"]


def test_set_password_success_prefights_first_updates_with_stdin_and_excludes_secret(monkeypatch):
    captured = "unit-\\\"test\\\\captured\\nvalue"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        if cmd[1] == "preflight-update-password":
            assert kwargs.get("input") is None
            return _json_cp(cmd, {"success": True, "preflight": "ok"})
        if cmd[1] == "update-password":
            assert kwargs.get("input") == captured
            assert "--password-stdin" in cmd
            return _json_cp(
                cmd,
                {
                    "success": True,
                    "updated": True,
                    "message": f"stored {captured}",
                    "password": captured,
                },
            )
        raise AssertionError(f"unexpected command: {cmd}")

    import tools.skills_tool as skills_tool

    monkeypatch.setattr(bws.subprocess, "run", fake_run)
    monkeypatch.setattr(
        skills_tool,
        "capture_transient_secret",
        lambda *args, **kwargs: {"success": True, "value": captured},
    )

    raw = bws.bitwarden_set_website_password("openrouter", "openrouter.ai")
    result = _load_result(raw)

    assert result["success"] is True
    assert [call["cmd"][1] for call in calls] == ["preflight-update-password", "update-password"]
    assert calls[1]["input"] == captured
    assert captured not in raw
    assert captured not in result.get("message", "")


def test_status_tool_returns_sanitized_json(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd == [bws._TOOL, "status"]
        return _json_cp(
            cmd,
            {
                "success": True,
                "helper": "ok",
                "bitwarden": {"authenticated": True, "token_value": "redacted by sanitizer"},
                "nested": {"cookie": "redacted by sanitizer", "safe": True},
            },
        )

    monkeypatch.setattr(bws.subprocess, "run", fake_run)

    result = _load_result(bws.bitwarden_safe_status())

    assert result["success"] is True
    assert result["helper"] == "ok"
    assert result["bitwarden"] == {"authenticated": True}
    assert result["nested"] == {"safe": True}


def test_alias_list_returns_aliases_and_no_item_ids_or_passwords(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd == [bws._TOOL, "list-aliases"]
        return _json_cp(
            cmd,
            {
                "success": True,
                "aliases": [
                    {
                        "alias": "openrouter",
                        "kind": "website_login",
                        "allowed_domains": ["openrouter.ai"],
                        "operation": "login_only",
                        "browser_profile": "abbyclaw1",
                        "high_risk_after_login": [],
                        "item_id": "item-id-1",
                        "password": "redacted by sanitizer",
                    },
                    {
                        "alias": "quicken-simplifi",
                        "kind": "website_login",
                        "allowed_domains": ["simplifi.quicken.com"],
                        "operation": "login_only",
                        "browser_profile": "abbyclaw1",
                        "high_risk_after_login": ["transaction_edit"],
                        "item_id": "item-id-2",
                    },
                ],
            },
        )

    monkeypatch.setattr(bws.subprocess, "run", fake_run)

    raw = bws.bitwarden_list_website_aliases()
    result = _load_result(raw)

    assert result["success"] is True
    assert result["aliases"] == [
        {
            "alias": "openrouter",
            "allowed_domains": ["openrouter.ai"],
            "kind": "website_login",
            "operation": "login_only",
            "browser_profile": "abbyclaw1",
            "high_risk_after_login": [],
        },
        {
            "alias": "quicken-simplifi",
            "allowed_domains": ["simplifi.quicken.com"],
            "kind": "website_login",
            "operation": "login_only",
            "browser_profile": "abbyclaw1",
            "high_risk_after_login": ["transaction_edit"],
        },
    ]
    assert "item-id-1" not in raw
    assert "item-id-2" not in raw


def test_prepare_login_missing_grant_returns_sanitized_failure(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        return _json_cp(
            cmd,
            {
                "success": False,
                "error": "missing browser profile grant",
                "secret_required": True,
                "token_value": "redacted by sanitizer",
            },
            returncode=1,
        )

    monkeypatch.setattr(bws.subprocess, "run", fake_run)

    result = _load_result(
        bws.bitwarden_prepare_website_login("openrouter", "openrouter.ai", profile="missing", dry_run=True)
    )

    assert result["success"] is False
    assert result["secret_required"] is True
    assert "grant" in result["error"]
    assert calls[0]["cmd"] == [
        bws._TOOL,
        "login-website",
        "--alias",
        "openrouter",
        "--domain",
        "openrouter.ai",
        "--profile",
        "missing",
        "--dry-run",
    ]


def test_prepare_login_success_does_not_return_password(monkeypatch):
    hidden = "unit-test-login-value"

    def fake_run(cmd, **kwargs):
        assert cmd == [
            bws._TOOL,
            "login-website",
            "--alias",
            "openrouter",
            "--domain",
            "openrouter.ai",
            "--profile",
            "abbyclaw1",
        ]
        return _json_cp(
            cmd,
            {
                "success": True,
                "prepared": True,
                "password": hidden,
                "nested": {"totp": "redacted by sanitizer", "ok": True},
            },
        )

    monkeypatch.setattr(bws.subprocess, "run", fake_run)

    raw = bws.bitwarden_prepare_website_login("openrouter", "openrouter.ai")
    result = _load_result(raw)

    assert result["success"] is True
    assert result["prepared"] is True
    assert result["nested"] == {"ok": True}
    assert hidden not in raw


def test_all_bitwarden_safe_tools_are_registered_and_in_toolsets():
    from tools.registry import registry
    from toolsets import TOOLSETS, _HERMES_CORE_TOOLS, resolve_toolset

    expected = {
        "bitwarden_safe_status",
        "bitwarden_list_website_aliases",
        "bitwarden_set_website_password",
        "bitwarden_prepare_website_login",
    }

    for name in expected:
        entry = registry.get_entry(name)
        assert entry is not None
        assert entry.toolset == "bitwarden_safe"
        assert entry.schema["name"] == name

    assert registry.get_entry("bitwarden_safe_status").check_fn is bws._check_bitwarden_safe_helper
    assert registry.get_entry("bitwarden_list_website_aliases").check_fn is bws._check_bitwarden_safe_helper
    assert registry.get_entry("bitwarden_set_website_password").check_fn is bws._check_bitwarden_safe_requirements
    assert registry.get_entry("bitwarden_prepare_website_login").check_fn is bws._check_bitwarden_safe_requirements
    assert expected.issubset(set(registry.get_tool_names_for_toolset("bitwarden_safe")))
    assert expected.issubset(set(TOOLSETS["bitwarden_safe"]["tools"]))
    assert expected.isdisjoint(set(_HERMES_CORE_TOOLS))
    assert expected.issubset(set(resolve_toolset("bitwarden_safe")))


def test_run_helper_json_handles_non_json_and_returncode_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(cmd, returncode=2, stdout="", stderr="plain failure")

    monkeypatch.setattr(bws.subprocess, "run", fake_run)

    result = _load_result(bws._run_helper_json([bws._TOOL, "status"]))

    assert result["success"] is False
    assert result["error"] == "plain failure"


def test_run_helper_json_redacts_non_json_secret_patterns(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(
            cmd,
            returncode=2,
            stdout="",
            stderr="api_key=abc123 session=def456 {'password': 'ghi\\'789'}",
        )

    monkeypatch.setattr(bws.subprocess, "run", fake_run)

    raw = bws._run_helper_json([bws._TOOL, "status"])
    result = _load_result(raw)

    assert result["success"] is False
    assert "abc123" not in raw
    assert "def456" not in raw
    assert "ghi789" not in raw
    assert "[REDACTED]" in result["error"]


def test_short_sensitive_named_env_values_do_not_corrupt_json(monkeypatch):
    monkeypatch.setenv("EXAMPLE_TOKEN", "true")
    raw = bws._safe_json({"success": True, "secret_exposed_to_llm": False})
    assert json.loads(raw) == {"success": True, "secret_exposed_to_llm": False}


def test_tool_arg_normalization_returns_safe_json_for_non_string_inputs():
    raw = bws.bitwarden_set_website_password(123, None)
    result = _load_result(raw)
    assert result["success"] is False
    assert "required" in result["error"]


def test_run_helper_json_handles_subprocess_exception(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

    monkeypatch.setattr(bws.subprocess, "run", fake_run)

    result = _load_result(bws._run_helper_json([bws._TOOL, "status"]))

    assert result["success"] is False
    assert "failed to run" in result["error"]
