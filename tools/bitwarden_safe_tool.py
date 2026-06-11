from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

from tools.registry import registry

_TOOL = "hermes-bitwarden-safe"
_SECRET_ENV_NAME = "HBS_TRANSIENT_WEBSITE_PASSWORD"
_SENSITIVE_FIELD_RE = re.compile(r"(password|passwd|pwd|secret|token|api[_-]?key|session|cookie|totp|otp)", re.I)
_SECRET_PATTERNS = [
    re.compile(r"\bBW_SESSION\S*", re.I),
    re.compile(r'''(?i)(["']?(password|passwd|pwd|token|secret|api[_-]?key|cookie|session)["']?\s*[:=]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')'''),
    re.compile(r"(?i)([\"']?(password|passwd|pwd|token|secret|api[_-]?key|cookie|session)[\"']?\s*[:=]\s*)[^\s,'\"]+"),
]
_ALLOWED_SENSITIVE_KEYS = {
    "secret_exposed_to_llm",
    "secret_required",
    "secret_values_exposed_to_llm",
    "secret_names_used",
    "bw_session_present",
    "bw_session_source",
    "bw_session_file_present",
}
_FORBIDDEN_SECRET_KEYS = {
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


def _replacement(match: str) -> str:
    m = re.match(
        r"(?i)((password|passwd|pwd|token|secret|api[_-]?key|cookie|session)\s*[:=]\s*)",
        match,
    )
    return (m.group(1) + "[REDACTED]") if m else "[REDACTED]"


def _known_env_secret_values() -> list[str]:
    values: list[str] = []
    for key, value in os.environ.items():
        if value and len(value) >= 8 and _SENSITIVE_FIELD_RE.search(key):
            values.append(value)
    return values


def _redact(text: Any, secrets: list[str]) -> str:
    out = "" if text is None else str(text)
    for secret in list(secrets or []) + _known_env_secret_values():
        if secret:
            out = out.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(lambda m: _replacement(m.group(0)), out)
    return out


def _sanitize_obj(obj: Any, secrets: list[str] | None = None) -> Any:
    """Remove fields that may contain secrets before JSON reaches the model."""
    secrets = secrets or []
    if isinstance(obj, dict):
        sanitized: dict[str, Any] = {}
        for key, value in obj.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if key_lower in _FORBIDDEN_SECRET_KEYS:
                continue
            if _SENSITIVE_FIELD_RE.search(key_text) and key_lower not in _ALLOWED_SENSITIVE_KEYS:
                continue
            sanitized[key_text] = _sanitize_obj(value, secrets)
        return sanitized
    if isinstance(obj, list):
        return [_sanitize_obj(item, secrets) for item in obj]
    if isinstance(obj, tuple):
        return [_sanitize_obj(item, secrets) for item in obj]
    if isinstance(obj, str):
        return _redact(obj, secrets)
    return obj


def _safe_json(obj: dict[str, Any], secrets: list[str] | None = None) -> str:
    secrets = secrets or []
    safe_obj = _sanitize_obj(obj, secrets)
    serialized = json.dumps(safe_obj, sort_keys=True)
    for secret in secrets:
        if secret:
            serialized = serialized.replace(secret, "[REDACTED]")
    return serialized


def _run_helper_json(
    cmd: list[str],
    timeout: int = 30,
    input_text: str | None = None,
    secrets: list[str] | None = None,
) -> str:
    """Run hermes-bitwarden-safe and return sanitized JSON for LLM consumption."""
    secrets = secrets or []
    try:
        cp = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as e:
        return _safe_json(
            {
                "success": False,
                "error": f"Bitwarden helper failed to run: {e}",
                "secret_exposed_to_llm": False,
            },
            secrets,
        )

    raw = cp.stdout.strip() or cp.stderr.strip()
    try:
        parsed = json.loads(raw) if raw else {}
        data = parsed if isinstance(parsed, dict) else {"success": False, "error": "Bitwarden helper returned non-object JSON"}
    except Exception:
        data = {
            "success": False,
            "error": _redact(raw or "Bitwarden helper returned non-JSON output", secrets),
        }

    if cp.returncode != 0:
        data["success"] = False
        if "error" not in data:
            data["error"] = _redact(cp.stderr or cp.stdout or "Bitwarden helper failed", secrets)

    data["secret_exposed_to_llm"] = False
    return _safe_json(data, secrets)


def _check_bitwarden_safe_helper() -> bool:
    return shutil.which(_TOOL) is not None


def _check_bitwarden_safe_requirements() -> bool:
    return _check_bitwarden_safe_helper() and shutil.which("bw") is not None


def _required_args_error(*names: str) -> str:
    return json.dumps(
        {
            "success": False,
            "error": f"{', '.join(names)} are required" if len(names) > 1 else f"{names[0]} is required",
            "secret_exposed_to_llm": False,
        },
        sort_keys=True,
    )


def _helper_succeeded(result_json: str) -> bool:
    try:
        result = json.loads(result_json)
    except Exception:
        return False
    return bool(isinstance(result, dict) and result.get("success"))


def _extract_aliases(data: dict[str, Any]) -> list[str]:
    candidates: Any = None
    for key in ("aliases", "website_aliases", "websites", "items"):
        if key in data:
            candidates = data[key]
            break
    if candidates is None and isinstance(data.get("config"), dict):
        config = data["config"]
        for key in ("aliases", "website_aliases", "websites", "items"):
            if key in config:
                candidates = config[key]
                break

    aliases: set[str] = set()
    if isinstance(candidates, dict):
        aliases.update(str(key) for key in candidates.keys() if str(key).strip())
    elif isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, str) and item.strip():
                aliases.add(item)
            elif isinstance(item, dict):
                alias = item.get("alias") or item.get("name")
                if alias and str(alias).strip():
                    aliases.add(str(alias))
    return sorted(aliases)


def _extract_alias_details(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: Any = data.get("aliases")
    details: list[dict[str, Any]] = []
    if isinstance(candidates, dict):
        candidates = [{"alias": alias, **(value if isinstance(value, dict) else {})} for alias, value in candidates.items()]
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, str):
                alias = item.strip()
                if alias:
                    details.append({"alias": alias, "allowed_domains": []})
            elif isinstance(item, dict):
                alias = item.get("alias") or item.get("name")
                if not alias or not str(alias).strip():
                    continue
                details.append(
                    {
                        "alias": str(alias),
                        "allowed_domains": list(item.get("allowed_domains") or []),
                        "kind": item.get("kind"),
                        "operation": item.get("operation"),
                        "browser_profile": item.get("browser_profile"),
                        "high_risk_after_login": list(item.get("high_risk_after_login") or []),
                    }
                )
    return sorted(details, key=lambda row: row["alias"])


def bitwarden_safe_status() -> str:
    """Return sanitized hermes-bitwarden-safe status information."""
    return _run_helper_json([_TOOL, "status"], timeout=30)


def bitwarden_list_website_aliases() -> str:
    """Return configured website aliases and allowed domains without Bitwarden item IDs or secrets."""
    raw = _run_helper_json([_TOOL, "list-aliases"], timeout=30)
    try:
        data = json.loads(raw)
    except Exception:
        return raw

    if not isinstance(data, dict) or not data.get("success"):
        return _safe_json(data if isinstance(data, dict) else {"success": False, "error": "invalid helper response"})

    aliases = _extract_alias_details(data)
    return _safe_json(
        {
            "success": True,
            "aliases": aliases,
            "count": len(aliases),
            "secret_exposed_to_llm": False,
        }
    )


def bitwarden_prepare_website_login(
    alias: str,
    domain: str,
    profile: str = "abbyclaw1",
    dry_run: bool = False,
) -> str:
    """Prepare a browser login using hermes-bitwarden-safe without returning secrets."""
    alias = str(alias or "").strip()
    domain = str(domain or "").strip()
    profile = str(profile or "").strip()
    if not alias or not domain or not profile:
        return _required_args_error("alias", "domain", "profile")

    cmd = [
        _TOOL,
        "login-website",
        "--alias",
        alias,
        "--domain",
        domain,
        "--profile",
        profile,
    ]
    if dry_run:
        cmd.append("--dry-run")
    return _run_helper_json(cmd, timeout=60)


def bitwarden_set_website_password(alias: str, domain: str) -> str:
    """Capture a new website password locally and update the configured Bitwarden item."""
    alias = str(alias or "").strip()
    domain = str(domain or "").strip()
    if not alias or not domain:
        return _required_args_error("alias", "domain")

    preflight = _run_helper_json(
        [
            _TOOL,
            "preflight-update-password",
            "--alias",
            alias,
            "--domain",
            domain,
        ],
        timeout=30,
    )
    if not _helper_succeeded(preflight):
        return preflight

    try:
        from tools.skills_tool import capture_transient_secret

        capture = capture_transient_secret(
            _SECRET_ENV_NAME,
            f"New Bitwarden password for {alias} ({domain})",
            {
                "purpose": "bitwarden_password_update",
                "alias": alias,
                "domain": domain,
                "help": "The password is captured for one-time use, sent to the local Bitwarden helper over stdin, and not stored in Hermes .env.",
            },
        )
    except Exception as e:
        return _safe_json(
            {
                "success": False,
                "error": f"secure secret capture failed: {_redact(e, [])}",
                "secret_exposed_to_llm": False,
            }
        )

    if not isinstance(capture, dict) or not capture.get("success") or capture.get("skipped"):
        return _safe_json(
            {
                "success": False,
                "error": "secure secret capture was cancelled or is unavailable",
                "secret_exposed_to_llm": False,
            }
        )

    password = str(capture.get("value") or "")
    if not password:
        return _safe_json(
            {
                "success": False,
                "error": "captured password was empty",
                "secret_exposed_to_llm": False,
            }
        )

    cmd = [
        _TOOL,
        "update-password",
        "--alias",
        alias,
        "--domain",
        domain,
        "--password-stdin",
    ]
    return _run_helper_json(cmd, timeout=75, input_text=password, secrets=[password])


registry.register(
    name="bitwarden_safe_status",
    toolset="bitwarden_safe",
    schema={
        "name": "bitwarden_safe_status",
        "description": "Return sanitized hermes-bitwarden-safe and Bitwarden CLI status information. No secrets are returned.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    handler=lambda args, **kw: bitwarden_safe_status(),
    check_fn=_check_bitwarden_safe_helper,
    requires_env=[],
    is_async=False,
    description="Show sanitized Bitwarden safe helper status",
    emoji="🔐",
)

registry.register(
    name="bitwarden_list_website_aliases",
    toolset="bitwarden_safe",
    schema={
        "name": "bitwarden_list_website_aliases",
        "description": "List configured hermes-bitwarden-safe website aliases without exposing item IDs, passwords, or other secrets.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    handler=lambda args, **kw: bitwarden_list_website_aliases(),
    check_fn=_check_bitwarden_safe_helper,
    requires_env=[],
    is_async=False,
    description="List configured Bitwarden website aliases without secret material",
    emoji="🔐",
)

registry.register(
    name="bitwarden_set_website_password",
    toolset="bitwarden_safe",
    schema={
        "name": "bitwarden_set_website_password",
        "description": (
            "Securely preflight, then capture a new website password using Hermes' local secret-capture UI "
            "and update the configured Bitwarden Password Manager item through hermes-bitwarden-safe. "
            "The raw password is never returned to the model. Use only when the user explicitly asks "
            "to set/update a website password in Bitwarden."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "alias": {
                    "type": "string",
                    "description": "Configured hermes-bitwarden-safe alias, e.g. openrouter or quicken-simplifi.",
                },
                "domain": {
                    "type": "string",
                    "description": "Website domain to validate against the alias allowlist, e.g. openrouter.ai.",
                },
            },
            "required": ["alias", "domain"],
            "additionalProperties": False,
        },
    },
    handler=lambda args, **kw: bitwarden_set_website_password(
        alias=args.get("alias", ""),
        domain=args.get("domain", ""),
    ),
    check_fn=_check_bitwarden_safe_requirements,
    requires_env=[],
    is_async=False,
    description="Securely update a configured Bitwarden website password via preflight and local secret capture",
    emoji="🔐",
)

registry.register(
    name="bitwarden_prepare_website_login",
    toolset="bitwarden_safe",
    schema={
        "name": "bitwarden_prepare_website_login",
        "description": "Prepare a website login through hermes-bitwarden-safe without returning passwords or other secret values.",
        "parameters": {
            "type": "object",
            "properties": {
                "alias": {
                    "type": "string",
                    "description": "Configured hermes-bitwarden-safe website alias.",
                },
                "domain": {
                    "type": "string",
                    "description": "Website domain to validate against the alias allowlist.",
                },
                "profile": {
                    "type": "string",
                    "description": "Browser/profile grant name to use for login preparation.",
                    "default": "abbyclaw1",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Validate and prepare without performing the login action.",
                    "default": False,
                },
            },
            "required": ["alias", "domain"],
            "additionalProperties": False,
        },
    },
    handler=lambda args, **kw: bitwarden_prepare_website_login(
        alias=args.get("alias", ""),
        domain=args.get("domain", ""),
        profile=args.get("profile", "abbyclaw1"),
        dry_run=bool(args.get("dry_run", False)),
    ),
    check_fn=_check_bitwarden_safe_requirements,
    requires_env=[],
    is_async=False,
    description="Prepare a configured Bitwarden website login without exposing secret material",
    emoji="🔐",
)
