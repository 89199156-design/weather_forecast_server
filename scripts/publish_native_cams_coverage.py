#!/usr/bin/env python3
"""Atomically publish an immutable native CAMS Open-Meteo runtime coverage."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any

from publish_native_om_coverage import (
    atomic_symlink,
    atomic_write_json,
    directory_stats,
    ensure_staging_is_scoped,
    load_coverage_manifests,
    promote_or_reuse_coverage,
    read_latest,
    validate_run_metadata,
)
from native_grid_contract import cams_domain_grids


UTC = timezone.utc
CAMS_THREE_HOUR_VARIABLES = {
    "carbon_monoxide",
    "dust",
    "nitrogen_dioxide",
    "ozone",
    "sulphur_dioxide",
}
DEFAULT_CAMS_REQUIRED_VARIABLES = "pm2_5,pm10,aerosol_optical_depth,dust,carbon_monoxide,nitrogen_dioxide,ozone,sulphur_dioxide"
DEFAULT_GREENHOUSE_REQUIRED_VARIABLES = "carbon_monoxide"


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def publish_cams_coverage(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).resolve()
    staging = Path(args.staging_dir).resolve()
    ensure_staging_is_scoped(staging, output_root)
    if not staging.is_dir():
        raise ValueError(f"staging directory does not exist: {staging}")
    if args.keep_coverages < 1:
        raise ValueError("keep_coverages must be positive")

    latest_metadata = read_latest(staging, "cams_global", args.run)
    if args.latest_max_forecast_hour != 120:
        raise ValueError("all retained CAMS runs must contain the complete 0...120h horizon")
    valid_times = [parse_time(value) for value in latest_metadata["valid_times"]]
    source_runs = [item.strip() for item in args.source_runs.split(",") if item.strip()]
    if len(source_runs) != 3:
        raise ValueError(f"CAMS coverage must contain three source runs, got {len(source_runs)}")
    parsed_runs = [datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=UTC) for run in source_runs]
    if parsed_runs[-1].strftime("%Y%m%d%H") != args.run:
        raise ValueError("latest CAMS run must be the final source run")
    if any(right - left != timedelta(hours=12) for left, right in zip(parsed_runs, parsed_runs[1:])):
        raise ValueError("CAMS source runs must be three consecutive 12-hour cycles")
    greenhouse_source_runs = [
        item.strip()
        for item in args.greenhouse_source_runs.split(",")
        if item.strip()
    ]
    if len(greenhouse_source_runs) != 3:
        raise ValueError(
            "CAMS greenhouse coverage must contain three source runs, "
            f"got {len(greenhouse_source_runs)}"
        )
    parsed_greenhouse_runs = [
        datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=UTC)
        for run in greenhouse_source_runs
    ]
    if any(run.hour != 0 for run in parsed_greenhouse_runs):
        raise ValueError("CAMS greenhouse source runs must use the official 00 UTC cycle")
    if any(
        right - left != timedelta(days=1)
        for left, right in zip(parsed_greenhouse_runs, parsed_greenhouse_runs[1:])
    ):
        raise ValueError("CAMS greenhouse source runs must be three consecutive daily cycles")
    expected_greenhouse_latest = parsed_runs[-1].replace(hour=0)
    if parsed_greenhouse_runs[-1] != expected_greenhouse_latest:
        raise ValueError("latest CAMS greenhouse run must be the latest CAMS run's 00 UTC day")
    public_start = parse_time(args.public_start_utc)
    public_end = parse_time(args.public_end_utc)
    public_hours = int((public_end - public_start).total_seconds() // 3600)
    if public_hours != args.public_hours:
        raise ValueError("CAMS public_hours does not match public start/end")
    expected_end = parsed_runs[-1] + timedelta(hours=args.latest_max_forecast_hour)
    if public_end != expected_end or max(valid_times) != expected_end:
        raise ValueError("CAMS public end does not match the latest complete run")
    if not parsed_runs[0] <= public_start <= parsed_runs[-1]:
        raise ValueError("CAMS public start is outside the three-run history window")
    if public_start != parsed_runs[0]:
        raise ValueError("CAMS public start must equal the oldest retained run")
    local_day_start = parse_time(args.local_day_start_utc)
    if not public_start <= local_day_start <= parsed_runs[-1]:
        raise ValueError("CAMS local day start is outside the retained history window")
    expected_cams_hours = list(range(args.latest_max_forecast_hour + 1))
    required_variables = {
        item.strip()
        for item in getattr(args, "required_variables", DEFAULT_CAMS_REQUIRED_VARIABLES).split(",")
        if item.strip()
    }
    for source_run in source_runs:
        run_meta = validate_run_metadata(
            staging,
            "cams_global",
            source_run,
            expected_cams_hours,
        )
        available_variables = set(run_meta["variables"])
        missing_variables = sorted(required_variables - available_variables)
        if missing_variables:
            raise ValueError(
                f"cams_global run {source_run} is missing required variables: "
                f"{','.join(missing_variables)}"
            )
        stored_counts = {
            variable: (args.latest_max_forecast_hour // 3 + 1)
            if variable in CAMS_THREE_HOUR_VARIABLES
            else args.latest_max_forecast_hour + 1
            for variable in run_meta["variables"]
        }
        validate_run_metadata(
            staging,
            "cams_global",
            source_run,
            expected_cams_hours,
            stored_counts,
        )
    expected_greenhouse_hours = list(range(0, 121, 3))
    greenhouse_required_variables = {
        item.strip()
        for item in getattr(
            args,
            "greenhouse_required_variables",
            DEFAULT_GREENHOUSE_REQUIRED_VARIABLES,
        ).split(",")
        if item.strip()
    }
    read_latest(staging, "cams_global_greenhouse_gases", greenhouse_source_runs[-1])
    for greenhouse_run in greenhouse_source_runs:
        run_meta = validate_run_metadata(
            staging,
            "cams_global_greenhouse_gases",
            greenhouse_run,
            expected_greenhouse_hours,
        )
        available_variables = set(run_meta["variables"])
        missing_variables = sorted(greenhouse_required_variables - available_variables)
        if missing_variables:
            raise ValueError(
                f"cams_global_greenhouse_gases run {greenhouse_run} is missing required variables: "
                f"{','.join(missing_variables)}"
            )
        validate_run_metadata(
            staging,
            "cams_global_greenhouse_gases",
            greenhouse_run,
            expected_greenhouse_hours,
            {variable: 41 for variable in run_meta["variables"]},
        )
    domain_grids = cams_domain_grids(
        getattr(args, "left_lon", 70.0),
        getattr(args, "right_lon", 140.0),
        getattr(args, "bottom_lat", 0.0),
        getattr(args, "top_lat", 58.0),
    )

    revision = (getattr(args, "coverage_revision", None) or "").strip()
    if revision and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", revision):
        raise ValueError("coverage_revision must match [a-z0-9][a-z0-9_-]{0,31}")
    coverage_id = f"cams_native_{args.run}"
    if revision:
        coverage_id = f"{coverage_id}_{revision}"
    coverage_relative = Path("coverages") / "cams" / coverage_id
    coverage_root = output_root / coverage_relative

    files, bytes_total = directory_stats(staging)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    coverage_manifest = {
        "version": 1,
        "status": "complete",
        "runtime_format": "openmeteo-native-v1",
        "group": "cams",
        "coverage_id": coverage_id,
        "latest_complete_run": args.run,
        "source_runs": source_runs,
        "greenhouse_source_runs": greenhouse_source_runs,
        "latest_max_forecast_hour": args.latest_max_forecast_hour,
        "public_start_utc": public_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "local_day_start_utc": args.local_day_start_utc,
        "public_end_utc": public_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "public_hours": public_hours,
        "domains": ["cams_global", "cams_global_greenhouse_gases"],
        "domain_grids": domain_grids,
        "files": files,
        "bytes": bytes_total,
        "generated_at": generated_at,
    }
    coverage_manifest, reused = promote_or_reuse_coverage(
        staging,
        coverage_root,
        coverage_manifest,
    )
    files = int(coverage_manifest["files"])
    bytes_total = int(coverage_manifest["bytes"])
    generated_at = str(coverage_manifest["generated_at"])

    ready = {
        "version": 1,
        "status": "complete",
        "runtime_format": "openmeteo-native-v1",
        "group": "cams",
        "release_id": coverage_id,
        "coverage_id": coverage_id,
        "latest_complete_run": args.run,
        "source_runs": source_runs,
        "greenhouse_source_runs": greenhouse_source_runs,
        "public_start_utc": coverage_manifest["public_start_utc"],
        "local_day_start_utc": args.local_day_start_utc,
        "public_end_utc": coverage_manifest["public_end_utc"],
        "public_hours": public_hours,
        "coverage_path": coverage_relative.as_posix(),
        "products": {
            "cams_global": {
                "coverage_id": coverage_id,
                "runtime_domain": "cams_global",
                "grid": domain_grids["cams_global"],
            },
            "cams_global_greenhouse_gases": {
                "coverage_id": coverage_id,
                "runtime_domain": "cams_global_greenhouse_gases",
                "grid": domain_grids["cams_global_greenhouse_gases"],
            }
        },
        "domain_grids": domain_grids,
        "files": files,
        "bytes": bytes_total,
        "generated_at": generated_at,
        "coverage_reused": reused,
    }
    atomic_write_json(
        output_root / "groups" / "cams" / "releases" / f"{coverage_id}.json",
        ready,
    )
    atomic_symlink(Path("..") / coverage_relative, output_root / "current" / "cams")
    atomic_write_json(
        output_root / "groups" / "cams" / "current" / "ready_for_processing.json",
        ready,
    )

    manifests = load_coverage_manifests(coverage_root.parent)
    retention_count = max(args.keep_coverages, 2) if revision else args.keep_coverages
    retained = {coverage_root.resolve()}
    for candidate, _ in manifests:
        resolved = candidate.resolve()
        if resolved in retained:
            continue
        if len(retained) < retention_count:
            retained.add(resolved)
    for old_root, _ in manifests:
        resolved = old_root.resolve()
        if resolved.parent != coverage_root.parent.resolve():
            raise ValueError(f"refusing to prune coverage outside root: {resolved}")
        if resolved in retained:
            continue
        shutil.rmtree(resolved)

    return ready


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish native CAMS Open-Meteo coverage")
    parser.add_argument("--staging-dir", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--source-runs", required=True)
    parser.add_argument("--greenhouse-source-runs", required=True)
    parser.add_argument("--latest-max-forecast-hour", type=int, required=True)
    parser.add_argument("--public-start-utc", required=True)
    parser.add_argument("--public-end-utc", required=True)
    parser.add_argument("--public-hours", type=int, required=True)
    parser.add_argument("--local-day-start-utc", required=True)
    parser.add_argument("--keep-coverages", type=int, default=3)
    parser.add_argument("--coverage-revision")
    parser.add_argument("--required-variables", default=os.environ.get("WEATHER_CAMS_VARIABLES", DEFAULT_CAMS_REQUIRED_VARIABLES))
    parser.add_argument(
        "--greenhouse-required-variables",
        default=os.environ.get(
            "WEATHER_CAMS_GREENHOUSE_VARIABLES",
            DEFAULT_GREENHOUSE_REQUIRED_VARIABLES,
        ),
    )
    parser.add_argument("--left-lon", type=float, default=float(os.environ.get("WEATHER_REGION_LEFT_LON", "70.0")))
    parser.add_argument("--right-lon", type=float, default=float(os.environ.get("WEATHER_REGION_RIGHT_LON", "140.0")))
    parser.add_argument("--bottom-lat", type=float, default=float(os.environ.get("WEATHER_REGION_BOTTOM_LAT", "0.0")))
    parser.add_argument("--top-lat", type=float, default=float(os.environ.get("WEATHER_REGION_TOP_LAT", "58.0")))
    args = parser.parse_args()

    try:
        ready = publish_cams_coverage(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(ready, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
