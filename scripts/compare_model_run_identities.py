#!/usr/bin/env python3
"""Prove Shanghai and Singapore use the same current GFS/CAMS source runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any


GROUPS = ("gfs", "cams")
RUN_IN_COVERAGE = re.compile(r"_(\d{10})(?:_|$)")
RUN_IN_NATIVE_PATH = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/(\d{2})00Z/")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _marker_coverage_ids(marker: dict[str, Any]) -> list[str]:
    coverage_ids: set[str] = set()
    coverage_id = str(marker.get("coverage_id") or "")
    if coverage_id:
        coverage_ids.add(coverage_id)
    for product in (marker.get("products") or {}).values() if isinstance(marker.get("products"), dict) else []:
        if isinstance(product, dict) and product.get("coverage_id"):
            coverage_ids.add(str(product["coverage_id"]))
    for product in (marker.get("product_manifests") or {}).values():
        if isinstance(product, dict) and product.get("coverage_id"):
            coverage_ids.add(str(product["coverage_id"]))
    return sorted(coverage_ids)


def inspect_live_snapshot(process_pid: int, groups: dict[str, Any]) -> dict[str, Any]:
    fd_root = Path("/proc") / str(process_pid) / "fd"
    if process_pid <= 0 or not fd_root.is_dir():
        raise ValueError(f"API process is not available: {process_pid}")
    targets: list[str] = []
    for descriptor in fd_root.iterdir():
        try:
            target = os.readlink(descriptor)
        except OSError:
            continue
        if ".om" in target or "coverages" in target:
            targets.append(target)

    by_group: dict[str, Any] = {}
    all_consistent = True
    for group, marker in groups.items():
        expected = list(marker.get("coverage_ids") or [])
        relevant = [
            target for target in targets
            if f"/coverages/{group}/" in target
            or (group == "gfs" and any(f"/{name}/coverages/" in target for name in ("gfs013_surface", "gfs025", "gfs_pressure_profile")))
            or (group == "cams" and any(f"/{name}/coverages/" in target for name in ("cams_global", "cams_global_greenhouse_gases")))
        ]
        loaded = sorted({coverage for coverage in expected if any(f"/{coverage}/" in target for target in relevant)})
        source_runs: set[str] = set()
        for target in relevant:
            source_runs.update(RUN_IN_COVERAGE.findall(target))
            match = RUN_IN_NATIVE_PATH.search(target)
            if match:
                source_runs.add("".join(match.groups()))
        consistent = bool(expected) and set(expected) <= set(loaded)
        all_consistent &= consistent
        by_group[group] = {
            "expected_coverage_ids": expected,
            "loaded_coverage_ids": loaded,
            "source_runs": sorted(source_runs),
            "open_coverage_files": len(relevant),
            "deleted_coverage_files": sum(target.endswith(" (deleted)") for target in relevant),
            "marker_matches_live_snapshot": consistent,
        }
    return {
        "pid": process_pid,
        "checked_at": int(time.time()),
        "marker_matches_live_snapshot": all_consistent,
        "groups": by_group,
    }


def build_identity(data_root: Path, process_pid: int | None = None) -> dict[str, Any]:
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
            "coverage_ids": _marker_coverage_ids(marker),
            "marker_sha256": hashlib.sha256(marker_bytes).hexdigest(),
        }
    identity = {"version": 3, "collected_at": int(time.time()), "groups": groups}
    if process_pid is not None:
        identity["live_snapshot"] = inspect_live_snapshot(process_pid, groups)
    return identity


def compare_identities(shanghai: dict[str, Any], singapore: dict[str, Any]) -> dict[str, Any]:
    mismatches = []
    matched_runs = {}
    live_snapshot_verified = True
    for endpoint, identity in (("shanghai", shanghai), ("singapore", singapore)):
        live = identity.get("live_snapshot")
        if not isinstance(live, dict) or live.get("marker_matches_live_snapshot") is not True:
            live_snapshot_verified = False
            mismatches.append(
                {
                    "endpoint": endpoint,
                    "reason": "marker_does_not_prove_live_api_snapshot",
                    "live_snapshot": live,
                }
            )
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
        "live_snapshot_verified": live_snapshot_verified,
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
    inventory.add_argument("--process-pid", type=int)
    inventory.add_argument("--output", required=True)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--shanghai-identity", required=True)
    compare.add_argument("--singapore-identity", required=True)
    compare.add_argument("--output-report", required=True)
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            payload = build_identity(Path(args.data_root), args.process_pid)
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
