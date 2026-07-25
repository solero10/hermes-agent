"""Guardrails for the compact repository context loaded by agents."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
REFERENCE_DOCS = (
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    "TESTING.md",
    "PITFALLS.md",
)
STALE_DOC_PHRASES = (
    "4 xdist workers",
    "`-n auto` xdist workers",
    "Hermes currently loads only the root `AGENTS.md`",
    "AGENTS.md (section **Adding New Tools**)",
    "sección **Adding New Tools**",
    "AGENTS.md#profiles-multi-instance-support",
    "python -m pytest tests/ -q",
    "python -m pytest tests/ -n0 -q",
    "pytest tests/ -v",
    "grep -r",
    "Run tests with xdist disabled",
    "使用禁用 xdist",
    "-n0 -q",
)
STALE_GUIDANCE_DOCS = (
    AGENTS_PATH,
    REPO_ROOT / "CONTRIBUTING.md",
    REPO_ROOT / "CONTRIBUTING.es.md",
    REPO_ROOT / "TESTING.md",
    REPO_ROOT / "gateway/platforms/ADDING_A_PLATFORM.md",
    REPO_ROOT / "README.zh-CN.md",
    REPO_ROOT / "website/docs/developer-guide/contributing.md",
    REPO_ROOT / "website/docs/developer-guide/adding-providers.md",
    REPO_ROOT
    / "website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/developer-guide/contributing.md",
    REPO_ROOT
    / "website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/developer-guide/adding-providers.md",
)
STALE_PITFALL_REFERENCE_FILES = (
    REPO_ROOT / "gateway/authz_mixin.py",
    REPO_ROOT / "cron/scheduler.py",
    REPO_ROOT / "tests/gateway/test_turn_lease.py",
    REPO_ROOT / "tests/gateway/test_conversation_scope_funnel.py",
    REPO_ROOT / "tests/gateway/test_empty_model_recovery.py",
    REPO_ROOT / "tests/gateway/test_queue_consumption.py",
)
LOCAL_MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.md(?:#[^)]+)?)\)")


def _local_path_from_link(target: str) -> Path:
    path_part = target.split("#", 1)[0]
    return REPO_ROOT / path_part


def test_agents_md_is_compact_operating_card_with_linked_references() -> None:
    assert AGENTS_PATH.is_file()

    agents_text = AGENTS_PATH.read_text(encoding="utf-8")
    assert len(agents_text.splitlines()) <= 250
    assert len(agents_text) <= 12_000
    assert "This is the always-loaded operating card" in agents_text

    for doc_name in REFERENCE_DOCS:
        assert f"[`{doc_name}`]({doc_name})" in agents_text
        assert (REPO_ROOT / doc_name).is_file()

    assert "## AIAgent Class (run_agent.py)" not in agents_text
    assert "## Known Pitfalls" not in agents_text
    assert "### Never read source code in tests" not in agents_text


def test_agents_md_local_reference_links_resolve() -> None:
    agents_text = AGENTS_PATH.read_text(encoding="utf-8")
    linked_paths = {
        _local_path_from_link(match.group(1))
        for match in LOCAL_MD_LINK_RE.finditer(agents_text)
    }
    assert linked_paths
    missing = sorted(
        str(path.relative_to(REPO_ROOT)) for path in linked_paths if not path.is_file()
    )
    assert missing == []


def test_repo_guidance_uses_current_v019_test_runner_contract() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in STALE_GUIDANCE_DOCS)

    for stale_phrase in STALE_DOC_PHRASES:
        assert stale_phrase not in combined

    testing_text = (REPO_ROOT / "TESTING.md").read_text(encoding="utf-8")
    assert "per-file subprocess" in testing_text
    assert "no xdist" in testing_text.lower()
    assert "scripts/run_tests_parallel.py" in testing_text


def test_repo_guidance_does_not_reference_removed_numbered_agents_pitfalls() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in STALE_PITFALL_REFERENCE_FILES
    )

    assert "pitfall #" not in combined.lower()
