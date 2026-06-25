#!/usr/bin/env python3
"""Validate local Open-Meteo point API coverage for GFS and CAMS."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from openmeteo_api_inventory import build_inventory  # noqa: E402


def generate_points(
    count: int,
    *,
    left_lon: float,
    right_lon: float,
    bottom_lat: float,
    top_lat: float,
) -> list[dict[str, float]]:
    if count <= 0:
        raise ValueError("count must be positive")
    points = []
    for index in range(count):
        fraction = (index + 0.5) / count
        latitude = bottom_lat + (top_lat - bottom_lat) * fraction
        longitude = left_lon + (right_lon - left_lon) * fraction
        points.append({"latitude": round(latitude, 6), "longitude": round(longitude, 6)})
    return points


def chunked(items: list[str], chunk_size: int) -> list[list[str]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def variables_for_scope(inventory: dict[str, Any], scope: str) -> list[str]:
    if scope == "gfs":
        return list(inventory["forecast"]["surface_api_variables"]) + list(inventory["forecast"]["pressure_api_variables"])
    if scope == "cams":
        return list(inventory["air_quality"]["raw_variables"]) + list(inventory["air_quality"]["derived_variables"])
    raise ValueError(f"unknown validation scope: {scope}")


def compare_series(local: list[Any], reference: list[Any], *, frames: int, tolerance: float) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for frame in range(frames):
        local_missing = frame >= len(local)
        reference_missing = frame >= len(reference)
        local_value = None if local_missing else local[frame]
        reference_value = None if reference_missing else reference[frame]
        if local_missing or reference_missing:
            mismatches.append(
                {
                    "frame": frame,
                    "local": local_value,
                    "reference": reference_value,
                    "reason": "length_mismatch",
                }
            )
            continue
        if local_value is None or reference_value is None:
            if local_value != reference_value:
                mismatches.append(
                    {
                        "frame": frame,
                        "local": local_value,
                        "reference": reference_value,
                        "reason": "null_mismatch",
                    }
                )
            continue
        if not _numbers_close(local_value, reference_value, tolerance):
            mismatches.append(
                {
                    "frame": frame,
                    "local": local_value,
                    "reference": reference_value,
                    "reason": "value_mismatch",
                }
            )
    return mismatches


def summarize_variable(series: list[Any] | None, *, frames: int) -> dict[str, Any]:
    if series is None:
        return {"status": "missing", "frames": 0, "nulls": frames}
    window = series[:frames]
    nulls = sum(1 for value in window if value is None)
    status = "all_null" if window and nulls == len(window) else "ok"
    if not window:
        status = "missing"
    return {"status": status, "frames": len(window), "nulls": nulls}


def _numbers_close(local_value: Any, reference_value: Any, tolerance: float) -> bool:
    try:
        local_float = float(local_value)
        reference_float = float(reference_value)
    except (TypeError, ValueError):
        return local_value == reference_value
    if math.isnan(local_float) or math.isnan(reference_float):
        return math.isnan(local_float) and math.isnan(reference_float)
    return abs(local_float - reference_float) <= tolerance


def fetch_json(base_url: str, endpoint: str, params: dict[str, Any], *, timeout: float) -> Any:
    url = base_url.rstrip("/") + endpoint + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def extract_hourly(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        if not payload:
            return {}
        payload = payload[0]
    if not isinstance(payload, dict):
        return {}
    hourly = payload.get("hourly")
    return hourly if isinstance(hourly, dict) else {}


def request_params(scope: str, point: dict[str, float], variables: list[str], frames: int) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {
        "latitude": point["latitude"],
        "longitude": point["longitude"],
        "hourly": ",".join(variables),
        "forecast_hours": frames,
        "timezone": "UTC",
        "timeformat": "iso8601",
    }
    if scope == "gfs":
        params["models"] = "gfs_global"
        params["wind_speed_unit"] = "ms"
        return "/v1/forecast", params
    if scope == "cams":
        params["domains"] = "cams_global"
        return "/v1/air-quality", params
    raise ValueError(f"unknown validation scope: {scope}")


def validate_scope(
    *,
    api_base_url: str,
    reference_base_url: str | None,
    scope: str,
    variables: list[str],
    points: list[dict[str, float]],
    frames: int,
    chunk_size: int,
    tolerance: float,
    timeout: float,
    allow_all_null: bool,
) -> dict[str, Any]:
    started = time.time()
    report: dict[str, Any] = {
        "scope": scope,
        "points": len(points),
        "frames": frames,
        "variables": len(variables),
        "reference_base_url": reference_base_url,
        "failures": [],
        "checked_values": 0,
    }

    for point_index, point in enumerate(points):
        for variable_chunk in chunked(variables, chunk_size):
            endpoint, params = request_params(scope, point, variable_chunk, frames)
            try:
                local_hourly = extract_hourly(fetch_json(api_base_url, endpoint, params, timeout=timeout))
            except Exception as exc:  # noqa: BLE001
                report["failures"].append(
                    {
                        "point_index": point_index,
                        "point": point,
                        "variables": variable_chunk,
                        "reason": "local_request_failed",
                        "error": str(exc),
                    }
                )
                continue

            reference_hourly = None
            if reference_base_url:
                try:
                    reference_hourly = extract_hourly(fetch_json(reference_base_url, endpoint, params, timeout=timeout))
                except Exception as exc:  # noqa: BLE001
                    report["failures"].append(
                        {
                            "point_index": point_index,
                            "point": point,
                            "variables": variable_chunk,
                            "reason": "reference_request_failed",
                            "error": str(exc),
                        }
                    )
                    continue

            for variable in variable_chunk:
                local_series = local_hourly.get(variable)
                summary = summarize_variable(local_series, frames=frames)
                report["checked_values"] += frames
                if summary["status"] == "missing" or (summary["status"] == "all_null" and not allow_all_null):
                    report["failures"].append(
                        {
                            "point_index": point_index,
                            "point": point,
                            "variable": variable,
                            "reason": summary["status"],
                            "summary": summary,
                        }
                    )
                    continue

                if reference_hourly is None:
                    continue

                reference_series = reference_hourly.get(variable)
                mismatches = compare_series(local_series or [], reference_series or [], frames=frames, tolerance=tolerance)
                if mismatches:
                    report["failures"].append(
                        {
                            "point_index": point_index,
                            "point": point,
                            "variable": variable,
                            "reason": "reference_mismatch",
                            "mismatch_count": len(mismatches),
                            "first_mismatches": mismatches[:10],
                        }
                    )

    report["elapsed_seconds"] = round(time.time() - started, 3)
    report["passed"] = not report["failures"]
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate local Open-Meteo GFS/CAMS point API output coverage.")
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--reference-base-url")
    parser.add_argument("--scope", choices=["gfs", "cams"], required=True)
    parser.add_argument("--points", type=int, required=True)
    parser.add_argument("--frames", type=int, default=50)
    parser.add_argument("--chunk-size", type=int, default=30)
    parser.add_argument("--tolerance", type=float, default=0.001)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--variables", help="Comma-separated override variable list.")
    parser.add_argument("--allow-all-null", action="store_true")
    parser.add_argument("--left-lon", type=float, default=70.0)
    parser.add_argument("--right-lon", type=float, default=140.0)
    parser.add_argument("--bottom-lat", type=float, default=0.0)
    parser.add_argument("--top-lat", type=float, default=58.0)
    parser.add_argument("--output-report", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inventory = build_inventory(Path(args.repo_root))
    variables = args.variables.split(",") if args.variables else variables_for_scope(inventory, args.scope)
    points = generate_points(
        args.points,
        left_lon=args.left_lon,
        right_lon=args.right_lon,
        bottom_lat=args.bottom_lat,
        top_lat=args.top_lat,
    )
    report = validate_scope(
        api_base_url=args.api_base_url,
        reference_base_url=args.reference_base_url,
        scope=args.scope,
        variables=variables,
        points=points,
        frames=args.frames,
        chunk_size=args.chunk_size,
        tolerance=args.tolerance,
        timeout=args.timeout,
        allow_all_null=args.allow_all_null,
    )
    output_path = Path(args.output_report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "failures": len(report["failures"]), "report": str(output_path)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
