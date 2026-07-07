#!/usr/bin/env python3
"""Verify an OpenBrain workflow video manifest and referenced recordings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_cli.openbrain_workflow_contracts import VideoManifest

MIN_DURATION_SECONDS = 10.0
MAX_DURATION_SECONDS = 40.0
EXPECTED_WIDTH = 1600
EXPECTED_HEIGHT = 900
DURATION_TOLERANCE_SECONDS = 0.75


def _probe_video(video_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None, "ffprobe not found"
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_frames:format=duration",
            "-of",
            "json",
            str(video_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None, f"ffprobe failed: {(result.stderr or result.stdout).strip()}"
    try:
        payload = json.loads(result.stdout)
        stream = payload.get("streams", [])[0]
        duration = float(payload.get("format", {}).get("duration", 0))
        return {
            "duration_seconds": duration,
            "width": int(stream.get("width", 0)),
            "height": int(stream.get("height", 0)),
            "frame_rate": stream.get("r_frame_rate"),
            "frames": int(stream.get("nb_frames") or 0),
        }, None
    except Exception as exc:  # noqa: BLE001 - verifier reports malformed media instead of crashing.
        return None, f"invalid ffprobe payload: {exc}"


def _decode_video(video_path: Path) -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return "ffmpeg not found"
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(video_path), "-f", "null", "-"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return f"ffmpeg decode failed: {(result.stderr or result.stdout).strip()}"
    return None


def verify_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest = VideoManifest.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - CLI should return structured errors.
        return {"ok": False, "workflow_run_id": None, "videos": 0, "errors": [f"invalid manifest: {exc}"]}

    errors: list[str] = []
    for video in manifest.videos:
        video_path = Path(video.path)
        if not video_path.exists():
            errors.append(f"missing video: {video.path}")
            continue
        if not video_path.is_file():
            errors.append(f"not a file: {video.path}")
            continue
        if not (MIN_DURATION_SECONDS <= float(video.duration_seconds) <= MAX_DURATION_SECONDS):
            errors.append(
                f"duration outside {MIN_DURATION_SECONDS:.0f}-{MAX_DURATION_SECONDS:.0f}s range: "
                f"{video.id} ({video.duration_seconds:.2f}s)"
            )
        if not video.shows_real_surface:
            errors.append(f"not marked real surface: {video.id}")
        if not video.annotations_visible:
            errors.append(f"annotations not visible: {video.id}")
        if not video.secrets_reviewed:
            errors.append(f"not secrets reviewed: {video.id}")

        probe, probe_error = _probe_video(video_path)
        if probe_error:
            errors.append(f"unreadable video: {video.id}: {probe_error}")
            continue
        assert probe is not None
        if not (MIN_DURATION_SECONDS <= probe["duration_seconds"] <= MAX_DURATION_SECONDS):
            errors.append(
                f"probed duration outside {MIN_DURATION_SECONDS:.0f}-{MAX_DURATION_SECONDS:.0f}s range: "
                f"{video.id} ({probe['duration_seconds']:.2f}s)"
            )
        if abs(float(video.duration_seconds) - float(probe["duration_seconds"])) > DURATION_TOLERANCE_SECONDS:
            errors.append(
                f"manifest/probed duration mismatch: {video.id} "
                f"manifest={video.duration_seconds:.2f}s probed={probe['duration_seconds']:.2f}s"
            )
        if probe["width"] != EXPECTED_WIDTH or probe["height"] != EXPECTED_HEIGHT:
            errors.append(
                f"unexpected dimensions: {video.id} "
                f"{probe['width']}x{probe['height']} != {EXPECTED_WIDTH}x{EXPECTED_HEIGHT}"
            )
        if probe["frames"] <= 0:
            errors.append(f"no decoded frame count: {video.id}")
        decode_error = _decode_video(video_path)
        if decode_error:
            errors.append(f"decode failed: {video.id}: {decode_error}")
    return {"ok": not errors, "workflow_run_id": manifest.workflow_run_id, "videos": len(manifest.videos), "errors": errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    args = parser.parse_args(argv)
    result = verify_manifest(Path(args.manifest).expanduser().resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
