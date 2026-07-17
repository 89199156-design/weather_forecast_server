#!/usr/bin/env python3
"""Validate a three-run native CAMS coverage through a shadow API."""

from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any
import urllib.error
import urllib.parse

from publish_native_om_coverage import validate_run_metadata
from validate_native_om_coverage import (
    DEFAULT_POINTS,
    fetch_api_json,
    iso_hour,
    load_json,
    parse_compact_run,
    parse_point,
    parse_utc,
    validate_api_payload,
)


DEFAULT_CAMS_VARIABLES = (
    "pm2_5",
    "pm10",
    "aerosol_optical_depth",
    "dust",
    "carbon_monoxide",
)


def validate_cams_contract(producer_root: Path) -> dict[str, Any]:
    producer_root = producer_root.resolve(strict=True)
    marker_path = producer_root / "groups" / "cams" / "current" / "ready_for_processing.json"
    if not marker_path.is_file():
        raise ValueError(f"missing ready marker: {marker_path}")
    marker = load_json(marker_path)
    relative_value = marker.get("coverage_path")
    if not isinstance(relative_value, str):
        raise ValueError("CAMS ready marker has no coverage_path")
    relative = PurePosixPath(relative_value)
    if relative.is_absolute() or ".." in relative.parts or relative.parts[:2] != ("coverages", "cams"):
        raise ValueError(f"unsafe CAMS coverage_path: {relative_value}")
    coverage = (producer_root / Path(*relative.parts)).resolve(strict=True)
    if coverage.parent != (producer_root / "coverages" / "cams").resolve(strict=True):
        raise ValueError("CAMS coverage resolves outside its group root")
    manifest = load_json(coverage / "coverage.json")
    identity_fields = (
        "status",
        "runtime_format",
        "group",
        "coverage_id",
        "latest_complete_run",
        "source_runs",
        "greenhouse_source_runs",
        "latest_max_forecast_hour",
        "public_start_utc",
        "local_day_start_utc",
        "public_end_utc",
        "public_hours",
        "domain_grids",
    )
    for field in identity_fields:
        if marker.get(field) != manifest.get(field):
            raise ValueError(f"CAMS marker/manifest mismatch for {field}")
    if marker.get("status") != "complete" or marker.get("runtime_format") != "openmeteo-native-v1":
        raise ValueError("CAMS native coverage is not complete")
    if marker.get("group") != "cams":
        raise ValueError("coverage group is not cams")
    current = producer_root / "current" / "cams"
    if not current.is_symlink() or current.resolve(strict=True) != coverage:
        raise ValueError("current CAMS pointer does not select the ready coverage")

    source_runs = marker.get("source_runs")
    if not isinstance(source_runs, list) or len(source_runs) != 3:
        raise ValueError("CAMS coverage must retain exactly three source runs")
    parsed_runs = [parse_compact_run(str(run)) for run in source_runs]
    if any(run.hour not in (0, 12) for run in parsed_runs):
        raise ValueError("CAMS source runs are not official 00/12 UTC cycles")
    if any(right - left != timedelta(hours=12) for left, right in zip(parsed_runs, parsed_runs[1:])):
        raise ValueError("CAMS source runs are not consecutive 12-hour cycles")
    if source_runs[-1] != marker.get("latest_complete_run"):
        raise ValueError("latest CAMS source run does not match marker")
    greenhouse_source_runs = marker.get("greenhouse_source_runs")
    if not isinstance(greenhouse_source_runs, list) or len(greenhouse_source_runs) != 3:
        raise ValueError("CAMS greenhouse coverage must retain exactly three source runs")
    parsed_greenhouse_runs = [
        parse_compact_run(str(run)) for run in greenhouse_source_runs
    ]
    if any(run.hour != 0 for run in parsed_greenhouse_runs):
        raise ValueError("CAMS greenhouse source runs are not 00 UTC cycles")
    if any(
        right - left != timedelta(days=1)
        for left, right in zip(parsed_greenhouse_runs, parsed_greenhouse_runs[1:])
    ):
        raise ValueError("CAMS greenhouse source runs are not consecutive daily cycles")
    if parsed_greenhouse_runs[-1] != (
        parsed_runs[-1].replace(hour=0) - timedelta(days=2)
    ):
        raise ValueError(
            "latest CAMS greenhouse source run does not match the official two-day release lag"
        )
    public_start = parse_utc(str(marker.get("public_start_utc")))
    public_end = parse_utc(str(marker.get("public_end_utc")))
    public_hours = int((public_end - public_start).total_seconds() // 3600)
    if marker.get("public_hours") != public_hours:
        raise ValueError("CAMS public_hours does not match public start/end")
    if not parsed_runs[0] <= public_start <= parsed_runs[-1]:
        raise ValueError("CAMS public start is outside the three-run history window")
    if public_start != parsed_runs[0]:
        raise ValueError("CAMS public start is not the oldest retained run")
    local_day_start = parse_utc(str(marker.get("local_day_start_utc")))
    if not public_start <= local_day_start <= parsed_runs[-1]:
        raise ValueError("CAMS local-day start is outside retained history")
    if public_end != parsed_runs[-1] + timedelta(hours=120):
        raise ValueError("CAMS public end is not latest run + 120h")
    if marker.get("latest_max_forecast_hour") != 120:
        raise ValueError("CAMS latest_max_forecast_hour is not 120")
    for domain in ("cams_global", "cams_global_greenhouse_gases"):
        grid = marker.get("domain_grids", {}).get(domain)
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
            raise ValueError(f"CAMS grid contract is incomplete for {domain}")
        if not (coverage / domain).is_dir():
            raise ValueError(f"missing {domain} runtime domain")
    for source_run in source_runs:
        run_meta = validate_run_metadata(
            coverage,
            "cams_global",
            source_run,
            list(range(121)),
        )
        validate_run_metadata(
            coverage,
            "cams_global",
            source_run,
            list(range(121)),
            {variable: 121 for variable in run_meta["variables"]},
        )
    for source_run in greenhouse_source_runs:
        run_meta = validate_run_metadata(
            coverage,
            "cams_global_greenhouse_gases",
            source_run,
            list(range(0, 121, 3)),
        )
        if "carbon_monoxide" not in run_meta["variables"]:
            raise ValueError(
                f"cams_global_greenhouse_gases run {source_run} is missing carbon_monoxide"
            )
        validate_run_metadata(
            coverage,
            "cams_global_greenhouse_gases",
            source_run,
            list(range(0, 121, 3)),
            {variable: 41 for variable in run_meta["variables"]},
        )
    return {
        "coverage_id": marker["coverage_id"],
        "coverage_path": str(coverage),
        "source_runs": source_runs,
        "greenhouse_source_runs": greenhouse_source_runs,
        "parsed_runs": parsed_runs,
        "public_start": public_start,
        "local_day_start": local_day_start,
        "public_end": public_end,
        "public_hours": public_hours,
    }


def build_cams_api_url(
    base_url: str,
    points: list[tuple[float, float]],
    variables: list[str],
    start: Any,
    end: Any,
) -> str:
    params = {
        "latitude": ",".join(str(point[0]) for point in points),
        "longitude": ",".join(str(point[1]) for point in points),
        "hourly": ",".join(variables),
        "timezone": "UTC",
        "timeformat": "iso8601",
        "start_hour": iso_hour(start),
        "end_hour": iso_hour(end),
        "domains": "cams_global",
    }
    return base_url.rstrip("/") + "/v1/air-quality?" + urllib.parse.urlencode(params)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-root", required=True)
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--point", action="append", type=parse_point)
    parser.add_argument("--variables", default=",".join(DEFAULT_CAMS_VARIABLES))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output-report", required=True)
    args = parser.parse_args()
    report: dict[str, Any] = {"passed": False, "failures": []}
    try:
        contract = validate_cams_contract(Path(args.producer_root))
        points = list(args.point or DEFAULT_POINTS)
        variables = [item.strip() for item in args.variables.split(",") if item.strip()]
        hours = [
            ("retained_window_start", contract["public_start"]),
            ("local_day_start", contract["local_day_start"]),
            ("latest_run", contract["parsed_runs"][-1]),
            ("latest_hour_120", contract["public_end"]),
        ]
        url = build_cams_api_url(
            args.api_base_url,
            points,
            variables,
            contract["public_start"],
            contract["public_end"],
        )
        api_result = validate_api_payload(
            fetch_api_json(url, args.timeout),
            points=points,
            variables=variables,
            hours=hours,
        )
        report = {
            "passed": api_result["passed"],
            "coverage_id": contract["coverage_id"],
            "coverage_path": contract["coverage_path"],
            "source_runs": contract["source_runs"],
            "public_start_utc": iso_hour(contract["public_start"]),
            "public_end_utc": iso_hour(contract["public_end"]),
            "public_hours": contract["public_hours"],
            "points": [{"latitude": lat, "longitude": lon} for lat, lon in points],
            "variables": variables,
            "failures": api_result["failures"],
            "critical_hours": api_result["critical_hours"],
        }
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        report["failures"] = [{"reason": "validation_error", "error": str(exc)}]
    output = Path(args.output_report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "failures": len(report["failures"]), "report": str(output)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
