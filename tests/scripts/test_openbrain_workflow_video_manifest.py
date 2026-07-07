from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_demo_fixture_script_writes_fixture(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "openbrain_workflow_demo_fixture.py"),
            "--workflow-run-id",
            "obwf_script_demo",
            "--source-unit-id",
            "script-source",
        ],
        text=True,
        capture_output=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["snapshot_json"]).exists()
    assert Path(payload["status_json"]).exists()


def test_record_and_verify_video_manifest_scripts(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe required for video verifier smoke")
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    video = tmp_path / "demo.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1600x900:r=8:d=10.25",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        cwd=str(REPO_ROOT),
    )
    manifest = tmp_path / "video-manifest.json"

    record = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "record_openbrain_workflow_videos.py"),
            "obwf_video_test",
            "--video",
            str(video),
            "--duration",
            "10.25",
            "--output",
            str(manifest),
        ],
        text=True,
        capture_output=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert record.returncode == 0, record.stderr
    assert manifest.exists()

    verify = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_openbrain_workflow_videos.py"), str(manifest)],
        text=True,
        capture_output=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert verify.returncode == 0, verify.stderr
    payload = json.loads(verify.stdout)
    assert payload == {"errors": [], "ok": True, "videos": 1, "workflow_run_id": "obwf_video_test"}


def test_verify_video_manifest_rejects_fake_too_short_video(tmp_path):
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"not a real mp4")
    manifest = tmp_path / "video-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workflow_run_id": "obwf_video_probe",
                "created_at": "2026-07-06T10:00:00Z",
                "videos": [
                    {
                        "id": "fake",
                        "path": str(video),
                        "duration_seconds": 1.5,
                        "shows_real_surface": True,
                        "annotations_visible": True,
                        "secrets_reviewed": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    verify = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_openbrain_workflow_videos.py"), str(manifest)],
        text=True,
        capture_output=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert verify.returncode == 1
    payload = json.loads(verify.stdout)
    assert payload["ok"] is False
    assert any("duration outside" in error for error in payload["errors"])
    assert any("unreadable video" in error for error in payload["errors"])


def test_verify_video_manifest_reports_missing_video(tmp_path):
    manifest = tmp_path / "video-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workflow_run_id": "obwf_video_test",
                "created_at": "2026-07-06T10:00:00Z",
                "videos": [
                    {
                        "id": "missing",
                        "path": str(tmp_path / "missing.mp4"),
                        "duration_seconds": 1,
                        "shows_real_surface": True,
                        "annotations_visible": True,
                        "secrets_reviewed": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    verify = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_openbrain_workflow_videos.py"), str(manifest)],
        text=True,
        capture_output=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert verify.returncode == 1
    payload = json.loads(verify.stdout)
    assert payload["ok"] is False
    assert "missing video" in payload["errors"][0]
