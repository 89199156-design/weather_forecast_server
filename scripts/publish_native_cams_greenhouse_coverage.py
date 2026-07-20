#!/usr/bin/env python3
"""Publish the independent three-run CAMS ADS native coverage atomically."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from native_grid_contract import cams_domain_grids
from publish_native_om_coverage import (
    atomic_symlink,
    atomic_write_json,
    directory_stats,
    ensure_staging_is_scoped,
    producer_image_ref,
    promote_or_reuse_coverage,
    read_latest,
    retain_coverages_before_reload,
    protected_coverage_directories,
    select_coverage_id_for_publish,
    validate_run_metadata,
    validate_runtime_variables,
)


UTC = timezone.utc
DOMAIN = "cams_global_greenhouse_gases"
DEFAULT_REQUIRED_VARIABLES = "carbon_monoxide"


def parse_run(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d%H").replace(tzinfo=UTC)


def publish_greenhouse_coverage(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).resolve()
    staging = Path(args.staging_dir).resolve()
    ensure_staging_is_scoped(staging, output_root)
    if not staging.is_dir():
        raise ValueError(f"staging directory does not exist: {staging}")
    if args.keep_coverages < 1:
        raise ValueError("keep_coverages must be positive")
    if args.latest_max_forecast_hour != 120:
        raise ValueError("CAMS ADS runs must contain the complete 0...120h horizon")

    source_runs = [item.strip() for item in args.source_runs.split(",") if item.strip()]
    if len(source_runs) != 3:
        raise ValueError(
            f"CAMS ADS coverage must contain three source runs, got {len(source_runs)}"
        )
    parsed_runs = [parse_run(run) for run in source_runs]
    if any(run.hour != 0 for run in parsed_runs):
        raise ValueError("CAMS ADS source runs must use the official daily 00 UTC cycle")
    if any(
        right - left != timedelta(days=1)
        for left, right in zip(parsed_runs, parsed_runs[1:])
    ):
        raise ValueError("CAMS ADS source runs must be three consecutive daily cycles")
    if source_runs[-1] != args.run:
        raise ValueError("latest CAMS ADS run must be the final source run")

    read_latest(staging, DOMAIN, args.run)
    required_variables = {
        item.strip()
        for item in args.required_variables.split(",")
        if item.strip()
    }
    if not required_variables:
        raise ValueError("CAMS ADS required variables cannot be empty")
    expected_hours = list(range(0, args.latest_max_forecast_hour + 1, 3))
    for source_run in source_runs:
        metadata = validate_run_metadata(staging, DOMAIN, source_run, expected_hours)
        missing = sorted(required_variables - set(metadata["variables"]))
        if missing:
            raise ValueError(
                f"{DOMAIN} run {source_run} is missing required variables: "
                + ",".join(missing)
            )
        validate_run_metadata(
            staging,
            DOMAIN,
            source_run,
            expected_hours,
            {variable: len(expected_hours) for variable in metadata["variables"]},
        )
    validate_runtime_variables(staging, DOMAIN, metadata["variables"])

    revision = (args.coverage_revision or "").strip()
    if revision and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", revision):
        raise ValueError("coverage_revision must match [a-z0-9][a-z0-9_-]{0,31}")
    coverage_id = f"cams_greenhouse_native_{args.run}"
    if revision:
        coverage_id = f"{coverage_id}_{revision}"
    coverage_id = select_coverage_id_for_publish(
        output_root,
        "cams_greenhouse",
        coverage_id,
        args.run,
    )
    coverage_relative = Path("coverages") / "cams_greenhouse" / coverage_id
    coverage_root = output_root / coverage_relative
    protected_coverages = protected_coverage_directories(
        output_root,
        "cams_greenhouse",
    )

    grids = cams_domain_grids(
        args.left_lon,
        args.right_lon,
        args.bottom_lat,
        args.top_lat,
    )
    domain_grids = {DOMAIN: grids[DOMAIN]}
    public_start = parsed_runs[0]
    public_end = parsed_runs[-1] + timedelta(hours=args.latest_max_forecast_hour)
    public_hours = int((public_end - public_start).total_seconds() // 3600)
    files, bytes_total = directory_stats(staging)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    producer_image = producer_image_ref()
    coverage_manifest: dict[str, Any] = {
        "version": 1,
        "status": "complete",
        "runtime_format": "openmeteo-native-v1",
        "group": "cams_greenhouse",
        "coverage_id": coverage_id,
        "latest_complete_run": args.run,
        "source_runs": source_runs,
        "latest_max_forecast_hour": args.latest_max_forecast_hour,
        "public_start_utc": public_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "local_day_start_utc": public_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "public_end_utc": public_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "public_hours": public_hours,
        "domains": [DOMAIN],
        "domain_grids": domain_grids,
        "files": files,
        "bytes": bytes_total,
        "generated_at": generated_at,
    }
    if producer_image:
        coverage_manifest["producer_image"] = producer_image
    coverage_manifest, reused = promote_or_reuse_coverage(
        staging,
        coverage_root,
        coverage_manifest,
    )

    ready: dict[str, Any] = {
        "version": 1,
        "status": "complete",
        "runtime_format": "openmeteo-native-v1",
        "group": "cams_greenhouse",
        "release_id": coverage_id,
        "coverage_id": coverage_id,
        "latest_complete_run": args.run,
        "source_runs": source_runs,
        "latest_max_forecast_hour": args.latest_max_forecast_hour,
        "public_start_utc": coverage_manifest["public_start_utc"],
        "local_day_start_utc": coverage_manifest["local_day_start_utc"],
        "public_end_utc": coverage_manifest["public_end_utc"],
        "public_hours": coverage_manifest["public_hours"],
        "coverage_path": coverage_relative.as_posix(),
        "products": {
            DOMAIN: {
                "coverage_id": coverage_id,
                "runtime_domain": DOMAIN,
                "grid": domain_grids[DOMAIN],
            }
        },
        "domain_grids": domain_grids,
        "files": int(coverage_manifest["files"]),
        "bytes": int(coverage_manifest["bytes"]),
        "generated_at": str(coverage_manifest["generated_at"]),
        "coverage_reused": reused,
    }
    if producer_image:
        ready["producer_image"] = producer_image

    atomic_write_json(
        output_root
        / "groups"
        / "cams_greenhouse"
        / "releases"
        / f"{coverage_id}.json",
        ready,
    )
    atomic_symlink(
        Path("..") / coverage_relative,
        output_root / "current" / "cams_greenhouse",
    )
    atomic_write_json(
        output_root
        / "groups"
        / "cams_greenhouse"
        / "current"
        / "ready_for_processing.json",
        ready,
    )

    retain_coverages_before_reload(
        coverage_root,
        args.keep_coverages,
        protected_coverages,
    )
    return ready


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--source-runs", required=True)
    parser.add_argument("--latest-max-forecast-hour", type=int, default=120)
    parser.add_argument("--keep-coverages", type=int, default=1)
    parser.add_argument("--coverage-revision", default="independent-v1")
    parser.add_argument(
        "--required-variables",
        default=os.environ.get(
            "WEATHER_CAMS_GREENHOUSE_VARIABLES",
            DEFAULT_REQUIRED_VARIABLES,
        ),
    )
    parser.add_argument(
        "--left-lon",
        type=float,
        default=float(os.environ.get("WEATHER_REGION_LEFT_LON", "70.0")),
    )
    parser.add_argument(
        "--right-lon",
        type=float,
        default=float(os.environ.get("WEATHER_REGION_RIGHT_LON", "140.0")),
    )
    parser.add_argument(
        "--bottom-lat",
        type=float,
        default=float(os.environ.get("WEATHER_REGION_BOTTOM_LAT", "0.0")),
    )
    parser.add_argument(
        "--top-lat",
        type=float,
        default=float(os.environ.get("WEATHER_REGION_TOP_LAT", "58.0")),
    )
    args = parser.parse_args()
    try:
        ready = publish_greenhouse_coverage(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(ready, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
