"""Guardrails for the compact repository context loaded by agents."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
REFERENCE_DOCS = (
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    "TESTING.md",
    "PITFALLS.md",
)


def test_agents_md_is_compact_operating_card_with_linked_references() -> None:
    assert AGENTS_PATH.is_file()

    agents_text = AGENTS_PATH.read_text(encoding="utf-8")
    assert len(agents_text.splitlines()) <= 250
    assert "This is the always-loaded operating card" in agents_text

    for doc_name in REFERENCE_DOCS:
        assert f"[`{doc_name}`]({doc_name})" in agents_text
        assert (REPO_ROOT / doc_name).is_file()
