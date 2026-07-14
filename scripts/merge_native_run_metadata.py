#!/usr/bin/env python3
"""Merge a partial Open-Meteo run download back into retained run metadata."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


def load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def merge_metadata(original: dict[str, Any], partial: dict[str, Any]) -> dict[str, Any]:
    if original.get("reference_time") != partial.get("reference_time"):
        raise ValueError("partial run reference_time does not match retained metadata")
    merged = dict(original)
    for field in ("created_at", "crs_wkt", "temporal_resolution_seconds"):
        if partial.get(field) is not None:
            merged[field] = partial[field]
    merged["variables"] = list(
        dict.fromkeys([*(original.get("variables") or []), *(partial.get("variables") or [])])
    )
    merged["valid_times"] = sorted(
        set([*(original.get("valid_times") or []), *(partial.get("valid_times") or [])])
    )
    if not merged["variables"] or not merged["valid_times"]:
        raise ValueError("merged run metadata is empty")
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--latest")
    args = parser.parse_args()
    try:
        original = load_object(Path(args.original))
        current_path = Path(args.current)
        merged = merge_metadata(original, load_object(current_path))
        atomic_write(current_path, merged)
        if args.latest:
            latest_path = Path(args.latest)
            latest = load_object(latest_path)
            if latest.get("reference_time") == merged.get("reference_time"):
                atomic_write(latest_path, merged)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"reference_time": merged["reference_time"],
                      "variables": len(merged["variables"]),
                      "valid_times": len(merged["valid_times"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
