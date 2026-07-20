#!/usr/bin/env python3
"""Report whether the API has acknowledged the current immutable coverage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


def pending_state(producer_root: Path, group: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", group):
        raise ValueError("invalid native group")
    marker_path = (
        producer_root / "groups" / group / "current" / "ready_for_processing.json"
    )
    if not marker_path.is_file():
        return "NONE"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("status") != "complete":
        return "NONE"
    coverage_id = str(marker.get("coverage_id") or "")
    run = str(marker.get("latest_complete_run") or "")
    if not coverage_id or not re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", coverage_id):
        raise ValueError("current native coverage identity is invalid")
    applied_path = producer_root / "groups" / group / "applied" / "current.json"
    if applied_path.is_file():
        try:
            applied = json.loads(applied_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            applied = {}
        if applied.get("coverage_id") == coverage_id:
            return f"APPLIED {run} {coverage_id}"
    return f"PENDING {run} {coverage_id}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-root", required=True)
    parser.add_argument("--group", required=True)
    args = parser.parse_args()
    try:
        print(pending_state(Path(args.producer_root), args.group))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
