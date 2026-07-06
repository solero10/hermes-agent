#!/usr/bin/env python3
"""Write a safe synthetic OpenBrain workflow/dashboard fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_cli.openbrain_workflow_demo import write_demo_fixture


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-run-id", default="obwf_demo")
    parser.add_argument("--source-unit-id", default="demo-transcript")
    args = parser.parse_args(argv)
    paths = write_demo_fixture(workflow_run_id=args.workflow_run_id, source_unit_id=args.source_unit_id)
    print(json.dumps(paths, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
