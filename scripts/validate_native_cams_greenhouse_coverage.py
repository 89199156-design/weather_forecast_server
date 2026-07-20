#!/usr/bin/env python3
"""Validate the independent three-run native CAMS ADS coverage."""

from __future__ import annotations

from datetime import timedelta
import argparse
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any

from publish_native_om_coverage import (
    read_latest,
    validate_run_metadata,
    validate_runtime_variables,
)
from validate_native_om_coverage import (
    load_json,
    parse_compact_run,
    parse_utc,
    validate_coverage_data_stats,
)


DOMAIN = "cams_global_greenhouse_gases"


def validate_greenhouse_contract(producer_root: Path) -> dict[str, Any]:
    producer_root = producer_root.resolve(strict=True)
    marker_path = (
        producer_root
        / "groups"
        / "cams_greenhouse"
        / "current"
        / "ready_for_processing.json"
    )
    if not marker_path.is_file():
        raise ValueError(f"missing ready marker: {marker_path}")
    marker = load_json(marker_path)
    relative_value = marker.get("coverage_path")
    if not isinstance(relative_value, str):
        raise ValueError("CAMS ADS ready marker has no coverage_path")
    relative = PurePosixPath(relative_value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[:2] != ("coverages", "cams_greenhouse")
    ):
        raise ValueError(f"unsafe CAMS ADS coverage_path: {relative_value}")
    coverage = (producer_root / Path(*relative.parts)).resolve(strict=True)
    expected_parent = (producer_root / "coverages" / "cams_greenhouse").resolve(
        strict=True
    )
    if coverage.parent != expected_parent:
        raise ValueError("CAMS ADS coverage resolves outside its group root")
    manifest = load_json(coverage / "coverage.json")
    for field in (
        "status",
        "runtime_format",
        "group",
        "coverage_id",
        "latest_complete_run",
        "source_runs",
        "latest_max_forecast_hour",
        "public_start_utc",
        "local_day_start_utc",
        "public_end_utc",
        "public_hours",
        "domain_grids",
        "files",
        "bytes",
    ):
        if marker.get(field) != manifest.get(field):
            raise ValueError(f"CAMS ADS marker/manifest mismatch for {field}")
    validate_coverage_data_stats(coverage, marker, manifest, "CAMS ADS")
    if (
        marker.get("status") != "complete"
        or marker.get("runtime_format") != "openmeteo-native-v1"
        or marker.get("group") != "cams_greenhouse"
    ):
        raise ValueError("CAMS ADS native coverage is not complete")
    current = producer_root / "current" / "cams_greenhouse"
    if not current.is_symlink() or current.resolve(strict=True) != coverage:
        raise ValueError("current CAMS ADS pointer does not select the ready coverage")

    source_runs = marker.get("source_runs")
    if not isinstance(source_runs, list) or len(source_runs) != 3:
        raise ValueError("CAMS ADS coverage must retain exactly three source runs")
    parsed_runs = [parse_compact_run(str(run)) for run in source_runs]
    if any(run.hour != 0 for run in parsed_runs):
        raise ValueError("CAMS ADS source runs are not 00 UTC cycles")
    if any(
        right - left != timedelta(days=1)
        for left, right in zip(parsed_runs, parsed_runs[1:])
    ):
        raise ValueError("CAMS ADS source runs are not consecutive daily cycles")
    if source_runs[-1] != marker.get("latest_complete_run"):
        raise ValueError("latest CAMS ADS source run does not match marker")
    if marker.get("latest_max_forecast_hour") != 120:
        raise ValueError("CAMS ADS latest_max_forecast_hour is not 120")
    public_start = parse_utc(str(marker.get("public_start_utc")))
    public_end = parse_utc(str(marker.get("public_end_utc")))
    if public_start != parsed_runs[0]:
        raise ValueError("CAMS ADS public start is not the oldest retained run")
    if public_end != parsed_runs[-1] + timedelta(hours=120):
        raise ValueError("CAMS ADS public end is not latest run + 120h")
    if marker.get("public_hours") != int(
        (public_end - public_start).total_seconds() // 3600
    ):
        raise ValueError("CAMS ADS public_hours does not match public start/end")
    products = marker.get("products")
    if not isinstance(products, dict) or set(products) != {DOMAIN}:
        raise ValueError("CAMS ADS marker must publish only the greenhouse product")
    grid = marker.get("domain_grids", {}).get(DOMAIN)
    if not isinstance(grid, dict) or not all(
        isinstance(grid.get(field), (int, float))
        for field in (
            "nx",
            "ny",
            "lat_min",
            "lon_min",
            "dx",
            "dy",
            "dt_seconds",
            "om_file_length",
        )
    ):
        raise ValueError("CAMS ADS grid contract is incomplete")
    if not (coverage / DOMAIN).is_dir():
        raise ValueError(f"missing {DOMAIN} runtime domain")
    read_latest(coverage, DOMAIN, str(source_runs[-1]))
    expected_hours = list(range(0, 121, 3))
    for source_run in source_runs:
        metadata = validate_run_metadata(
            coverage,
            DOMAIN,
            str(source_run),
            expected_hours,
        )
        if "carbon_monoxide" not in metadata["variables"]:
            raise ValueError(f"{DOMAIN} run {source_run} is missing carbon_monoxide")
        validate_run_metadata(
            coverage,
            DOMAIN,
            str(source_run),
            expected_hours,
            {variable: len(expected_hours) for variable in metadata["variables"]},
        )
    validate_runtime_variables(coverage, DOMAIN, metadata["variables"])
    return {
        "coverage_id": marker["coverage_id"],
        "coverage_path": str(coverage),
        "source_runs": [str(run) for run in source_runs],
        "latest_complete_run": str(source_runs[-1]),
        "public_start_utc": marker["public_start_utc"],
        "public_end_utc": marker["public_end_utc"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-root", required=True)
    args = parser.parse_args()
    try:
        result = validate_greenhouse_contract(Path(args.producer_root))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
