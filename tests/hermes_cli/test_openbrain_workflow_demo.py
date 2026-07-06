from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli.openbrain_workflow_demo import (
    build_video_manifest_entry,
    write_demo_fixture,
    write_video_manifest,
)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def test_write_demo_fixture_creates_snapshot_and_workflow_status(hermes_home):
    paths = write_demo_fixture(workflow_run_id="obwf_demo_test", source_unit_id="demo-source")
    for path in paths.values():
        assert Path(path).exists(), path

    snapshot = json.loads(Path(paths["snapshot_json"]).read_text(encoding="utf-8"))
    assert snapshot["source_units"][0]["id"] == "demo-source"
    assert snapshot["source_units"][0]["thoughts"][0]["workflow_statuses"]["enrich"]["status"] == "current"

    status = json.loads(Path(paths["status_json"]).read_text(encoding="utf-8"))
    assert status["workflow_run_id"] == "obwf_demo_test"
    assert status["completed_candidates"] == 1


def test_video_manifest_requires_secret_review_and_writes_manifest(hermes_home, tmp_path):
    with pytest.raises(ValueError):
        build_video_manifest_entry(
            path=str(tmp_path / "demo.mp4"),
            duration_seconds=3.5,
            shows_real_surface=True,
            annotations_visible=True,
            secrets_reviewed=False,
        )

    entry = build_video_manifest_entry(
        path=str(tmp_path / "demo.mp4"),
        duration_seconds=3.5,
        shows_real_surface=True,
        annotations_visible=True,
        secrets_reviewed=True,
    )
    manifest = write_video_manifest(workflow_run_id="obwf_demo_test", videos=[entry])
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["workflow_run_id"] == "obwf_demo_test"
    assert data["videos"][0]["secrets_reviewed"] is True
