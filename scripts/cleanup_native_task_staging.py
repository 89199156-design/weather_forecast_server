#!/usr/bin/env python3
"""Remove only stale staging directories owned by one self-locked task."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys


PATTERNS = {
    "gfs": (("staging", re.compile(r"gfs_\d{10}_\d+")),),
    "cams_ecpds": (("staging", re.compile(r"cams_\d{10}_\d+")),),
    "cams_ads": (
        ("staging", re.compile(r"cams_greenhouse_\d{10}_\d+")),
        ("ads_staging", re.compile(r"cams_ads_\d{10}")),
    ),
    "cams_ads_publish": (
        ("staging", re.compile(r"cams_greenhouse_\d{10}_\d+")),
    ),
}


def directory_bytes(path: Path) -> int:
    total = 0
    for candidate in path.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            total += candidate.stat().st_size
    return total


def cleanup(producer_root: Path, scope: str, keep_name: str | None) -> dict[str, object]:
    producer_root = producer_root.resolve()
    removed: list[str] = []
    removed_bytes = 0
    for relative_root, pattern in PATTERNS[scope]:
        root = producer_root / relative_root
        if not root.exists():
            continue
        if not root.is_dir() or root.is_symlink():
            raise ValueError(f"unsafe staging root: {root}")
        for candidate in list(root.iterdir()):
            if candidate.name == keep_name or not pattern.fullmatch(candidate.name):
                continue
            if not candidate.is_dir() or candidate.is_symlink():
                raise ValueError(f"unsafe stale staging entry: {candidate}")
            removed_bytes += directory_bytes(candidate)
            shutil.rmtree(candidate)
            removed.append(str(candidate.relative_to(producer_root)))
    return {
        "stage": "cleanup",
        "scope": scope,
        "removed_directories": len(removed),
        "removed_bytes": removed_bytes,
        "removed": removed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-root", required=True)
    parser.add_argument("--scope", choices=tuple(PATTERNS), required=True)
    parser.add_argument("--keep-name")
    args = parser.parse_args()
    if args.keep_name and not re.fullmatch(r"[a-z0-9_]+", args.keep_name):
        parser.error("--keep-name has an unsafe value")
    try:
        result = cleanup(
            Path(args.producer_root),
            args.scope,
            args.keep_name,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
