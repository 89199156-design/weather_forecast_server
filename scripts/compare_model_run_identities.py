#!/usr/bin/env python3
"""Prove Shanghai and Singapore use the same current GFS/CAMS source runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


GROUPS = ("gfs", "cams")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def build_identity(data_root: Path) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for group in GROUPS:
        marker_path = data_root / "groups" / group / "current" / "ready_for_processing.json"
        marker_bytes = marker_path.read_bytes()
        marker = json.loads(marker_bytes)
        latest = str(marker.get("latest_complete_run") or "")
        if marker.get("status") != "complete" or len(latest) != 10 or not latest.isdigit():
            raise ValueError(f"{group} marker is not a complete source run")
        products = marker.get("products")
        if isinstance(products, dict):
            product_names = sorted(products)
        elif isinstance(products, list):
            product_names = sorted(str(item) for item in products)
        else:
            product_names = sorted((marker.get("product_manifests") or {}).keys())
        groups[group] = {
            "latest_complete_run": latest,
            "source_runs": list(marker.get("source_runs") or []),
            "products": product_names,
            "coverage_id": str(marker.get("coverage_id") or marker.get("release_id") or ""),
            "marker_sha256": hashlib.sha256(marker_bytes).hexdigest(),
        }
    return {"version": 2, "collected_at": int(time.time()), "groups": groups}


def compare_identities(shanghai: dict[str, Any], singapore: dict[str, Any]) -> dict[str, Any]:
    mismatches = []
    matched_runs = {}
    for group in GROUPS:
        left = (shanghai.get("groups") or {}).get(group) or {}
        right = (singapore.get("groups") or {}).get(group) or {}
        left_run = left.get("latest_complete_run")
        right_run = right.get("latest_complete_run")
        if left_run != right_run or not left_run:
            mismatches.append(
                {
                    "group": group,
                    "reason": "latest_complete_run_mismatch",
                    "shanghai": left_run,
                    "singapore": right_run,
                }
            )
        else:
            matched_runs[group] = left_run
    return {
        "passed": not mismatches,
        "same_source_runs": not mismatches,
        "matched_latest_runs": matched_runs,
        "compared_at": int(time.time()),
        "inventory_collected_at": {
            "shanghai": shanghai.get("collected_at"),
            "singapore": singapore.get("collected_at"),
        },
        "mismatches": mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--data-root", required=True)
    inventory.add_argument("--output", required=True)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--shanghai-identity", required=True)
    compare.add_argument("--singapore-identity", required=True)
    compare.add_argument("--output-report", required=True)
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            payload = build_identity(Path(args.data_root))
            atomic_json(Path(args.output), payload)
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        shanghai = json.loads(Path(args.shanghai_identity).read_text(encoding="utf-8"))
        singapore = json.loads(Path(args.singapore_identity).read_text(encoding="utf-8"))
        report = compare_identities(shanghai, singapore)
        atomic_json(Path(args.output_report), report)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["passed"] else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
