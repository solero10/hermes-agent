#!/usr/bin/env python3
"""Verify an OpenBrain workflow video manifest and referenced recordings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_cli.openbrain_workflow_contracts import VideoManifest


def verify_manifest(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    manifest = VideoManifest.model_validate(payload)
    errors: list[str] = []
    for video in manifest.videos:
        video_path = Path(video.path)
        if not video_path.exists():
            errors.append(f"missing video: {video.path}")
        if video.duration_seconds <= 0:
            errors.append(f"non-positive duration: {video.id}")
        if not video.shows_real_surface:
            errors.append(f"not marked real surface: {video.id}")
        if not video.annotations_visible:
            errors.append(f"annotations not visible: {video.id}")
        if not video.secrets_reviewed:
            errors.append(f"not secrets reviewed: {video.id}")
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
