#!/usr/bin/env python3
"""Validate and atomically publish one regional ECMWF time-series database."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from ecmwf_contract import (
    MODEL,
    OPENMETEO_UPSTREAM_COMMIT,
    RAW_VARIABLES,
    STORAGE_BOUNDS,
    parse_run,
    source_run_plan,
)


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.tmp.{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, link)


def directory_stats(root: Path) -> tuple[int, int]:
    files = 0
    bytes_total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            files += 1
            bytes_total += path.stat().st_size
    return files, bytes_total


def validate_release(staging: Path, run: str) -> dict[str, object]:
    if not staging.is_dir():
        raise ValueError(f"missing ECMWF staging directory: {staging}")
    forbidden = [
        path
        for pattern in ("download-*", "http_cache", "data_run")
        for path in staging.glob(pattern)
        if path.exists()
    ]
    if forbidden:
        raise ValueError(
            "ECMWF staging retains transient or duplicate data: "
            + ",".join(str(path) for path in forbidden)
        )

    model_root = staging / MODEL
    meta_path = model_root / "static" / "meta.json"
    elevation_path = model_root / "static" / "HSURF.om"
    if not meta_path.is_file() or not elevation_path.is_file():
        raise ValueError("ECMWF model meta/elevation files are incomplete")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    expected_epoch = int(parse_run(run).timestamp())
    if int(meta.get("last_run_initialisation_time", -1)) != expected_epoch:
        raise ValueError("ECMWF model meta does not identify the target run")
    if int(meta.get("temporal_resolution_seconds", -1)) != 10800:
        raise ValueError("ECMWF native time resolution is not three hours")
    if int(meta.get("data_end_time", 0)) < expected_epoch + 360 * 3600:
        raise ValueError("ECMWF model meta does not cover forecast hour 360")

    missing = []
    for variable in RAW_VARIABLES:
        root = model_root / variable
        if not root.is_dir() or not any(
            path.is_file() and path.stat().st_size > 0
            for path in root.glob("*.om")
        ):
            missing.append(variable)
    if missing:
        raise ValueError(
            "ECMWF release is missing required native variables: "
            + ",".join(missing[:30])
        )
    return meta


def publish(
    root: Path,
    staging: Path,
    run: str,
    image: str,
    patch_sha256: str,
    source_revision: str,
) -> dict[str, object]:
    if not re.fullmatch(r"[a-f0-9]{64}", patch_sha256):
        raise ValueError("ECMWF patch SHA-256 is invalid")
    if not re.fullmatch(r"[a-f0-9]{40}", source_revision):
        raise ValueError("source revision must be a full lowercase Git commit")
    expected_staging_parent = (root / "staging").resolve()
    if staging.resolve().parent != expected_staging_parent:
        raise ValueError("ECMWF staging directory escapes its managed root")
    validate_release(staging, run)
    progress = json.loads(
        (staging / "production-progress.json").read_text(encoding="utf-8")
    )
    expected_runs = [item[0] for item in source_run_plan(run)]
    if progress.get("target_run") != run or progress.get("completed_runs") != expected_runs:
        raise ValueError("ECMWF production progress is not complete and ordered")

    coverage_id = f"ecmwf_ifs025_{run}_{source_revision[:12]}"
    coverage_root = root / "releases" / coverage_id
    if coverage_root.exists():
        raise ValueError(f"ECMWF immutable release already exists: {coverage_id}")
    files, bytes_total = directory_stats(staging)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker: dict[str, object] = {
        "version": 1,
        "status": "complete",
        "runtime_format": "openmeteo-timeseries-v1",
        "group": "ecmwf",
        "model": MODEL,
        "coverage_id": coverage_id,
        "release_id": coverage_id,
        "coverage_path": f"releases/{coverage_id}",
        "latest_complete_run": run,
        "latest_max_forecast_hour": 360,
        "hourly_frames": 361,
        "daily_frames": 15,
        "source_runs": expected_runs,
        "source_run_max_forecast_hours": [
            item[1] for item in source_run_plan(run)
        ],
        "required_variables": list(RAW_VARIABLES),
        "missing_required_variables": [],
        "optional_variables": [],
        "missing_optional_variables": [],
        "grid": {
            "grid_type": "regional_regular_lat_lon",
            "full_nx": 1440,
            "full_ny": 721,
            "x0": 992,
            "y0": 352,
            "nx": 297,
            "ny": 249,
            "dx": 0.25,
            "dy": 0.25,
            "lon_min": STORAGE_BOUNDS[0],
            "lat_min": STORAGE_BOUNDS[2],
            "requested_bounds": {
                "left_lon": STORAGE_BOUNDS[0],
                "right_lon": STORAGE_BOUNDS[1],
                "bottom_lat": STORAGE_BOUNDS[2],
                "top_lat": STORAGE_BOUNDS[3],
            },
        },
        "producer_image": image,
        "openmeteo_upstream_commit": OPENMETEO_UPSTREAM_COMMIT,
        "regional_patch_sha256": patch_sha256,
        "source_revision": source_revision,
        "files": files,
        "bytes": bytes_total,
        "generated_at": generated_at,
    }
    atomic_write_json(staging / "ready_for_processing.json", marker)
    coverage_root.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, coverage_root)
    atomic_symlink(
        Path("releases") / coverage_id,
        root / "current",
    )
    atomic_write_json(
        root / "groups" / "ecmwf" / "current" / "ready_for_processing.json",
        marker,
    )
    atomic_write_json(
        root / "groups" / "ecmwf" / "releases" / f"{coverage_id}.json",
        marker,
    )
    return marker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--staging", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--patch-sha256", required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    try:
        payload = publish(
            Path(args.root).resolve(),
            Path(args.staging).resolve(),
            args.run,
            args.image,
            args.patch_sha256,
            args.source_revision,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=os.sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
