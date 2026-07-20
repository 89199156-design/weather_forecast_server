#!/usr/bin/env python3
"""Align one native current symlink to its authoritative complete marker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from seed_native_om_staging import safe_current_coverage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-root", required=True)
    parser.add_argument("--group", choices=("gfs", "cams", "cams_greenhouse"), required=True)
    args = parser.parse_args()
    try:
        current = safe_current_coverage(Path(args.producer_root), args.group)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "stage": "cleanup",
                "group": args.group,
                "current": str(current[0]) if current else None,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
