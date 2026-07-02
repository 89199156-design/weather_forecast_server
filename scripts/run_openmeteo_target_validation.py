#!/usr/bin/env python3
"""Run targeted Open-Meteo API parity batches for client-used GFS/CAMS outputs."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_openmeteo_point_api import (  # noqa: E402
    default_start_hour,
    format_utc_hour,
    generate_points,
    parse_utc_hour,
    validate_scope,
)

GFS_TARGET_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "apparent_temperature",
    "precipitation",
    "rain",
    "showers",
    "snowfall",
    "snow_depth",
    "weather_code",
    "visibility",
    "cape",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "wind_u_component_10m",
    "wind_v_component_10m",
    "cloud_cover",
    "cloud_cover_high",
    "cloud_cover_mid",
    "cloud_cover_low",
    "pressure_msl",
    "surface_pressure",
    "uv_index",
    "uv_index_clear_sky",
    "is_day",
]

CAMS_TARGET_VARIABLES = [
    "pm2_5",
    "pm10",
    "carbon_monoxide",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "ozone",
    "aerosol_optical_depth",
    "dust",
    "uv_index",
    "uv_index_clear_sky",
    "us_aqi",
    "european_aqi",
    "ch_aqi",
    "ch_iaqi_pm2_5",
    "ch_iaqi_pm10",
    "ch_iaqi_so2",
    "ch_iaqi_no2",
    "ch_iaqi_o3",
    "ch_iaqi_co",
]


def point_key(point: dict[str, float]) -> tuple[float, float]:
    return (round(point["latitude"], 6), round(point["longitude"], 6))


def build_point_batches(
    *,
    batches: int,
    points_per_batch: int,
    left_lon: float,
    right_lon: float,
    bottom_lat: float,
    top_lat: float,
    point_offset: float,
) -> list[list[dict[str, float]]]:
    if batches <= 0:
        raise ValueError("batches must be positive")
    if points_per_batch <= 0:
        raise ValueError("points_per_batch must be positive")
    total_points = batches * points_per_batch
    points = generate_points(
        total_points,
        left_lon=left_lon,
        right_lon=right_lon,
        bottom_lat=bottom_lat,
        top_lat=top_lat,
        point_offset=point_offset,
    )
    unique_points = {point_key(point) for point in points}
    if len(unique_points) != len(points):
        raise ValueError("generated validation points are not unique")
    return [points[index : index + points_per_batch] for index in range(0, len(points), points_per_batch)]


def target_variables_for_scope(scope: str) -> list[str]:
    if scope == "gfs":
        return list(GFS_TARGET_VARIABLES)
    if scope == "cams":
        return list(CAMS_TARGET_VARIABLES)
    raise ValueError(f"unknown validation scope: {scope}")


def parse_variable_override(value: str | None, scope: str) -> list[str]:
    if not value:
        return target_variables_for_scope(scope)
    variables = [item.strip() for item in value.split(",") if item.strip()]
    if not variables:
        raise ValueError(f"{scope} variable override is empty")
    return variables


def summarize_batch_results(
    batch_results: list[dict[str, Any]],
    *,
    planned_batches: int,
    frames: int,
    points_per_batch: int,
    failure_limit: int,
    variables_by_scope: dict[str, list[str]],
) -> dict[str, Any]:
    failed_batches = sum(1 for result in batch_results if not result["passed"])
    stopped_reason = None
    if failed_batches >= failure_limit:
        stopped_reason = "failure_limit_reached"
    elif len(batch_results) < planned_batches:
        stopped_reason = "stopped_before_all_batches"
    return {
        "passed": len(batch_results) == planned_batches and failed_batches == 0,
        "planned_batches": planned_batches,
        "completed_batches": len(batch_results),
        "points_per_batch": points_per_batch,
        "planned_points": planned_batches * points_per_batch,
        "completed_points": len(batch_results) * points_per_batch,
        "frames": frames,
        "failed_batches": failed_batches,
        "failure_limit": failure_limit,
        "stopped_reason": stopped_reason,
        "variables_by_scope": variables_by_scope,
        "batch_results": batch_results,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def validate_batch(
    *,
    batch_index: int,
    points: list[dict[str, float]],
    scopes: list[str],
    variables_by_scope: dict[str, list[str]],
    api_base_urls: dict[str, str],
    reference_base_urls: dict[str, str | None],
    api_host_headers: dict[str, str | None],
    reference_host_headers: dict[str, str | None],
    runs: dict[str, str | None],
    request_forecast_hours_by_scope: dict[str, int | None],
    output_dir: Path,
    frames: int,
    start_hour: str,
    end_hour: str,
    chunk_size: int,
    tolerance: float,
    timeout: float,
    request_retries: int,
    request_retry_delay: float,
    request_pause: float,
    reference_ssh_host: str | None,
    allow_all_null: bool,
) -> dict[str, Any]:
    scope_results = []
    for scope in scopes:
        report_path = output_dir / f"batch-{batch_index:02d}-{scope}.json"
        report = validate_scope(
            api_base_url=api_base_urls[scope],
            reference_base_url=reference_base_urls[scope],
            scope=scope,
            variables=variables_by_scope[scope],
            points=points,
            frames=frames,
            chunk_size=chunk_size,
            point_chunk_size=len(points),
            sample_offset=0.0,
            start_hour=start_hour,
            end_hour=end_hour,
            tolerance=tolerance,
            timeout=timeout,
            allow_all_null=allow_all_null,
            request_retries=request_retries,
            request_retry_delay=request_retry_delay,
            request_pause=request_pause,
            api_host_header=api_host_headers[scope],
            reference_host_header=reference_host_headers[scope],
            reference_ssh_host=reference_ssh_host,
            run=runs[scope],
            request_forecast_hours=request_forecast_hours_by_scope[scope],
            progress_path=report_path.with_suffix(".progress.json"),
        )
        write_json(report_path, report)
        scope_results.append(
            {
                "scope": scope,
                "passed": report["passed"],
                "failures": len(report["failures"]),
                "checked_values": report["checked_values"],
                "report": str(report_path),
            }
        )
    return {
        "batch": batch_index,
        "passed": all(result["passed"] for result in scope_results),
        "points": points,
        "scopes": scope_results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run targeted Open-Meteo 10-point x 24-frame parity batches.")
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--reference-base-url")
    parser.add_argument("--api-host-header")
    parser.add_argument("--reference-host-header")
    parser.add_argument("--gfs-api-base-url")
    parser.add_argument("--cams-api-base-url")
    parser.add_argument("--gfs-reference-base-url")
    parser.add_argument("--cams-reference-base-url")
    parser.add_argument("--gfs-api-host-header")
    parser.add_argument("--cams-api-host-header")
    parser.add_argument("--gfs-reference-host-header")
    parser.add_argument("--cams-reference-host-header")
    parser.add_argument("--reference-ssh-host")
    parser.add_argument("--run")
    parser.add_argument("--gfs-run")
    parser.add_argument("--cams-run")
    parser.add_argument("--output-dir", default="docs/validation/reports/target")
    parser.add_argument("--scopes", default="gfs,cams")
    parser.add_argument("--batches", type=int, default=100)
    parser.add_argument("--points-per-batch", type=int, default=10)
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--failure-limit", type=int, default=3)
    parser.add_argument("--start-hour")
    parser.add_argument("--end-hour")
    parser.add_argument("--chunk-size", type=int, default=30)
    parser.add_argument("--tolerance", type=float, default=0.001)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--request-retries", type=int, default=3)
    parser.add_argument("--request-retry-delay", type=float, default=2.0)
    parser.add_argument("--request-pause", type=float, default=0.0)
    parser.add_argument("--allow-all-null", action="store_true")
    parser.add_argument("--left-lon", type=float, default=70.0)
    parser.add_argument("--right-lon", type=float, default=140.0)
    parser.add_argument("--bottom-lat", type=float, default=0.0)
    parser.add_argument("--top-lat", type=float, default=58.0)
    parser.add_argument("--point-offset", type=float, default=0.0)
    parser.add_argument("--gfs-variables")
    parser.add_argument("--cams-variables")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.failure_limit <= 0:
        raise ValueError("--failure-limit must be positive")
    scopes = [scope.strip() for scope in args.scopes.split(",") if scope.strip()]
    variables_by_scope = {
        scope: parse_variable_override(
            args.gfs_variables if scope == "gfs" else args.cams_variables if scope == "cams" else None,
            scope,
        )
        for scope in scopes
    }
    start_dt = parse_utc_hour(args.start_hour) if args.start_hour else default_start_hour()
    end_dt = parse_utc_hour(args.end_hour) if args.end_hour else start_dt + timedelta(hours=args.frames - 1)
    start_hour = format_utc_hour(start_dt)
    end_hour = format_utc_hour(end_dt)
    api_base_urls: dict[str, str] = {}
    reference_base_urls: dict[str, str | None] = {}
    api_host_headers: dict[str, str | None] = {}
    reference_host_headers: dict[str, str | None] = {}
    runs: dict[str, str | None] = {}
    request_forecast_hours_by_scope: dict[str, int | None] = {}
    for scope in scopes:
        api_base_urls[scope] = (
            args.gfs_api_base_url
            if scope == "gfs" and args.gfs_api_base_url
            else args.cams_api_base_url
            if scope == "cams" and args.cams_api_base_url
            else args.api_base_url
        )
        reference_base_urls[scope] = (
            args.gfs_reference_base_url
            if scope == "gfs" and args.gfs_reference_base_url
            else args.cams_reference_base_url
            if scope == "cams" and args.cams_reference_base_url
            else args.reference_base_url
        )
        api_host_headers[scope] = (
            args.gfs_api_host_header
            if scope == "gfs" and args.gfs_api_host_header
            else args.cams_api_host_header
            if scope == "cams" and args.cams_api_host_header
            else args.api_host_header
        )
        reference_host_headers[scope] = (
            args.gfs_reference_host_header
            if scope == "gfs" and args.gfs_reference_host_header
            else args.cams_reference_host_header
            if scope == "cams" and args.cams_reference_host_header
            else args.reference_host_header
        )
        run_arg = (
            args.gfs_run
            if scope == "gfs" and args.gfs_run
            else args.cams_run
            if scope == "cams" and args.cams_run
            else args.run
            if scope == "gfs" and args.run
            else None
        )
        run = format_utc_hour(parse_utc_hour(run_arg)) if run_arg else None
        runs[scope] = run
        request_forecast_hours = None
        if run:
            run_dt = parse_utc_hour(run)
            if end_dt < run_dt:
                raise ValueError(f"--end-hour must not be before {scope} run")
            request_forecast_hours = int((end_dt - run_dt).total_seconds() // 3600) + 1
        request_forecast_hours_by_scope[scope] = request_forecast_hours

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    point_batches = build_point_batches(
        batches=args.batches,
        points_per_batch=args.points_per_batch,
        left_lon=args.left_lon,
        right_lon=args.right_lon,
        bottom_lat=args.bottom_lat,
        top_lat=args.top_lat,
        point_offset=args.point_offset,
    )

    batch_results: list[dict[str, Any]] = []
    failures = 0
    started = time.time()
    for batch_index, points in enumerate(point_batches, start=1):
        result = validate_batch(
            batch_index=batch_index,
            points=points,
            scopes=scopes,
            variables_by_scope=variables_by_scope,
            api_base_urls=api_base_urls,
            reference_base_urls=reference_base_urls,
            api_host_headers=api_host_headers,
            reference_host_headers=reference_host_headers,
            runs=runs,
            request_forecast_hours_by_scope=request_forecast_hours_by_scope,
            output_dir=output_dir,
            frames=args.frames,
            start_hour=start_hour,
            end_hour=end_hour,
            chunk_size=args.chunk_size,
            tolerance=args.tolerance,
            timeout=args.timeout,
            request_retries=args.request_retries,
            request_retry_delay=args.request_retry_delay,
            request_pause=args.request_pause,
            reference_ssh_host=args.reference_ssh_host,
            allow_all_null=args.allow_all_null,
        )
        batch_results.append(result)
        if not result["passed"]:
            failures += 1
        summary = summarize_batch_results(
            batch_results,
            planned_batches=args.batches,
            frames=args.frames,
            points_per_batch=args.points_per_batch,
            failure_limit=args.failure_limit,
            variables_by_scope=variables_by_scope,
        )
        summary["elapsed_seconds"] = round(time.time() - started, 3)
        write_json(output_dir / "summary.progress.json", summary)
        print(json.dumps({"batch": batch_index, "passed": result["passed"], "failed_batches": failures}), flush=True)
        if failures >= args.failure_limit:
            break

    summary = summarize_batch_results(
        batch_results,
        planned_batches=args.batches,
        frames=args.frames,
        points_per_batch=args.points_per_batch,
        failure_limit=args.failure_limit,
        variables_by_scope=variables_by_scope,
    )
    summary["elapsed_seconds"] = round(time.time() - started, 3)
    summary_path = output_dir / f"summary-{args.batches}x{args.points_per_batch}x{args.frames}.json"
    write_json(summary_path, summary)
    print(json.dumps({"passed": summary["passed"], "summary": str(summary_path)}, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
