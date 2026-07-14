#!/usr/bin/env python3
"""Repair a current GFS coverage whose metadata added a duplicate grid halo."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

from native_grid_contract import gfs_domain_grids
from om_v3_metadata import read_array_dimensions
from publish_native_om_coverage import atomic_write_json, product_contract, run_directory


UTC = timezone.utc
DOMAINS = ("ncep_gfs013", "ncep_gfs025")


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def repair(data_root: Path) -> dict[str, Any]:
    data_root = data_root.resolve()
    ready_path = data_root / "groups/gfs/current/ready_for_processing.json"
    ready = load_json(ready_path)
    if ready.get("status") != "complete" or ready.get("runtime_format") != "openmeteo-native-v1":
        raise ValueError("current GFS marker is not a complete native coverage")

    coverage_relative = Path(str(ready["coverage_path"]))
    if coverage_relative.is_absolute() or ".." in coverage_relative.parts:
        raise ValueError("unsafe GFS coverage path")
    coverage_root = (data_root / coverage_relative).resolve()
    expected_parent = (data_root / "coverages/gfs").resolve()
    if coverage_root.parent != expected_parent:
        raise ValueError("GFS coverage resolves outside the coverage root")
    if (data_root / "current/gfs").resolve() != coverage_root:
        raise ValueError("current GFS symlink does not match the ready marker")

    old_grids = ready.get("domain_grids") or {}
    requested = old_grids.get("ncep_gfs013", {}).get("requested_bounds")
    if not isinstance(requested, dict):
        raise ValueError("current GFS marker has no requested bounds")
    for domain in DOMAINS:
        if old_grids.get(domain, {}).get("requested_bounds") != requested:
            raise ValueError("GFS domains do not share one requested region")

    grids = gfs_domain_grids(
        float(requested["left_lon"]),
        float(requested["right_lon"]),
        float(requested["bottom_lat"]),
        float(requested["top_lat"]),
    )
    source_runs = ready.get("source_runs") or []
    checked_files = 0
    for domain in DOMAINS:
        expected = (grids[domain]["ny"], grids[domain]["nx"])
        for run in source_runs:
            root = run_directory(coverage_root, domain, str(run))
            meta = load_json(root / "meta.json")
            for variable in meta.get("variables") or []:
                dimensions = read_array_dimensions(root / f"{variable}.om")
                if dimensions[:2] != expected:
                    raise ValueError(
                        f"{domain} {run} {variable} grid {dimensions[:2]} does not match {expected}"
                    )
                checked_files += 1
    if checked_files == 0:
        raise ValueError("current GFS coverage contains no OM files")

    repaired_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    coverage_path = coverage_root / "coverage.json"
    coverage = load_json(coverage_path)
    coverage["domain_grids"] = grids
    coverage["grid_contract_repaired_at"] = repaired_at

    ready["domain_grids"] = grids
    ready["products"] = product_contract(str(ready["coverage_id"]), grids)
    ready["grid_contract_repaired_at"] = repaired_at
    release_path = data_root / "groups/gfs/releases" / f"{ready['release_id']}.json"

    atomic_write_json(coverage_path, coverage)
    atomic_write_json(release_path, ready)
    atomic_write_json(ready_path, ready)
    return {
        "status": "success",
        "coverage_id": ready["coverage_id"],
        "checked_om_files": checked_files,
        "domain_grids": grids,
        "repaired_at": repaired_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    args = parser.parse_args()
    try:
        result = repair(Path(args.data_root))
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
