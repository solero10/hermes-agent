#!/usr/bin/env python3
"""Create a video manifest for OpenBrain workflow proof recordings.

This helper intentionally does not fabricate videos. It records metadata for
already-captured recordings and writes the manifest under the File Browser-served
artifact root by default.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_constants import get_hermes_home
from hermes_cli.openbrain_workflow_artifacts import validate_workflow_run_id
from hermes_cli.openbrain_workflow_demo import build_video_manifest_entry, write_video_manifest


def artifact_manifest_path(workflow_run_id: str) -> Path:
    run_id = validate_workflow_run_id(workflow_run_id)
    return get_hermes_home() / "artifacts" / "openbrain-workflow-runs" / run_id / "video-manifest.json"


def probe_duration_seconds(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow_run_id")
    parser.add_argument("--video", action="append", required=True, help="Path to a reviewed mp4/webm proof video. Repeatable.")
    parser.add_argument("--duration", action="append", type=float, help="Duration seconds for corresponding --video when ffprobe is unavailable.")
    parser.add_argument("--output", default=None, help="Manifest path. Defaults to ~/.hermes/artifacts/openbrain-workflow-runs/<run>/video-manifest.json")
    parser.add_argument("--filebrowser-url", default=None)
    args = parser.parse_args(argv)

    durations = args.duration or []
    entries = []
    for idx, raw_path in enumerate(args.video):
        path = Path(raw_path).expanduser().resolve()
        if not path.exists():
            parser.error(f"video does not exist: {path}")
        duration = probe_duration_seconds(path)
        if duration is None:
            if idx >= len(durations):
                parser.error(f"duration required for {path} because ffprobe could not read it")
            duration = durations[idx]
        entries.append(
            build_video_manifest_entry(
                path=str(path),
                duration_seconds=duration,
                shows_real_surface=True,
                annotations_visible=True,
                secrets_reviewed=True,
                filebrowser_url=args.filebrowser_url,
            )
        )
    output = Path(args.output).expanduser().resolve() if args.output else artifact_manifest_path(args.workflow_run_id)
    manifest = write_video_manifest(workflow_run_id=args.workflow_run_id, videos=entries, output_path=output)
    print(json.dumps({"manifest": str(manifest), "videos": len(entries)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
