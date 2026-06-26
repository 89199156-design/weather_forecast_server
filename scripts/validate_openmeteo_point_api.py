#!/usr/bin/env python3
"""Validate local Open-Meteo point API coverage for GFS and CAMS."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - requests is available in supported validation environments.
    requests = None  # type: ignore[assignment]


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from openmeteo_api_inventory import build_inventory  # noqa: E402

REQUESTS_SESSION = requests.Session() if requests is not None else None
if REQUESTS_SESSION is not None:
    REQUESTS_SESSION.trust_env = False

URLLIB_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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
        return list(inventory["gfs_point_api"]["surface_variables"]) + list(inventory["gfs_point_api"]["pressure_variables"])
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


def fetch_json(
    base_url: str,
    endpoint: str,
    params: dict[str, Any],
    *,
    timeout: float,
    retries: int,
    retry_delay: float,
    request_pause: float,
) -> Any:
    url = base_url.rstrip("/") + endpoint + "?" + urllib.parse.urlencode(params)
    if REQUESTS_SESSION is not None:
        for attempt in range(retries + 1):
            try:
                response = REQUESTS_SESSION.get(url, headers={"Accept": "application/json"}, timeout=timeout)
                retryable = response.status_code == 429 or 500 <= response.status_code < 600
                if response.ok:
                    if request_pause > 0:
                        time.sleep(request_pause)
                    return response.json()
                if not retryable or attempt >= retries:
                    response.raise_for_status()
            except requests.RequestException:
                if attempt >= retries:
                    raise

            time.sleep(retry_delay * (2**attempt))

        raise RuntimeError("unreachable retry state")

    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    for attempt in range(retries + 1):
        try:
            with URLLIB_OPENER.open(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
            if request_pause > 0:
                time.sleep(request_pause)
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt >= retries:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt >= retries:
                raise

        time.sleep(retry_delay * (2**attempt))

    raise RuntimeError("unreachable retry state")


def extract_hourly(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        if not payload:
            return {}
        payload = payload[0]
    if not isinstance(payload, dict):
        return {}
    hourly = payload.get("hourly")
    return hourly if isinstance(hourly, dict) else {}


def extract_hourlies(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [extract_hourly(item) for item in payload]
    return [extract_hourly(payload)]


def format_points(points: list[dict[str, float]], key: str) -> str:
    return ",".join(str(point[key]) for point in points)


def request_params(
    scope: str,
    points: list[dict[str, float]],
    variables: list[str],
    frames: int,
    *,
    start_hour: str | None = None,
    end_hour: str | None = None,
) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {
        "latitude": format_points(points, "latitude"),
        "longitude": format_points(points, "longitude"),
        "hourly": ",".join(variables),
        "timezone": "UTC",
        "timeformat": "iso8601",
    }
    if start_hour and end_hour:
        params["start_hour"] = start_hour
        params["end_hour"] = end_hour
    else:
        params["forecast_hours"] = frames
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
    point_chunk_size: int,
    start_hour: str | None = None,
    end_hour: str | None = None,
    tolerance: float,
    timeout: float,
    allow_all_null: bool,
    request_retries: int,
    request_retry_delay: float,
    request_pause: float,
    progress_path: Path | None = None,
) -> dict[str, Any]:
    started = time.time()
    total_chunks = math.ceil(len(points) / point_chunk_size) * math.ceil(len(variables) / chunk_size)
    report: dict[str, Any] = {
        "scope": scope,
        "points": len(points),
        "frames": frames,
        "variables": len(variables),
        "reference_base_url": reference_base_url,
        "start_hour": start_hour,
        "end_hour": end_hour,
        "failures": [],
        "checked_values": 0,
        "completed_chunks": 0,
        "total_chunks": total_chunks,
    }

    def write_progress(point_offset: int, variable_chunk: list[str], stage: str) -> None:
        report["elapsed_seconds"] = round(time.time() - started, 3)
        report["passed"] = not report["failures"]
        if progress_path is None:
            return
        progress_payload = dict(report)
        progress_payload["incomplete"] = True
        progress_payload["current_stage"] = stage
        progress_payload["last_point_offset"] = point_offset
        progress_payload["last_variables"] = variable_chunk
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(
            json.dumps(progress_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def finish_chunk(point_offset: int, variable_chunk: list[str]) -> None:
        report["completed_chunks"] += 1
        write_progress(point_offset, variable_chunk, "chunk_finished")

    for point_offset in range(0, len(points), point_chunk_size):
        point_chunk = points[point_offset : point_offset + point_chunk_size]
        for variable_chunk in chunked(variables, chunk_size):
            endpoint, params = request_params(
                scope,
                point_chunk,
                variable_chunk,
                frames,
                start_hour=start_hour,
                end_hour=end_hour,
            )
            write_progress(point_offset, variable_chunk, "local_request_start")
            try:
                local_hourlies = extract_hourlies(
                    fetch_json(
                        api_base_url,
                        endpoint,
                        params,
                        timeout=timeout,
                        retries=request_retries,
                        retry_delay=request_retry_delay,
                        request_pause=request_pause,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                report["failures"].append(
                    {
                        "point_index": point_offset,
                        "points": point_chunk,
                        "variables": variable_chunk,
                        "reason": "local_request_failed",
                        "error": str(exc),
                    }
                )
                finish_chunk(point_offset, variable_chunk)
                continue

            reference_hourly = None
            if reference_base_url:
                write_progress(point_offset, variable_chunk, "reference_request_start")
                try:
                    reference_hourlies = extract_hourlies(
                        fetch_json(
                            reference_base_url,
                            endpoint,
                            params,
                            timeout=timeout,
                            retries=request_retries,
                            retry_delay=request_retry_delay,
                            request_pause=request_pause,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    report["failures"].append(
                        {
                            "point_index": point_offset,
                            "points": point_chunk,
                            "variables": variable_chunk,
                            "reason": "reference_request_failed",
                            "error": str(exc),
                        }
                    )
                    finish_chunk(point_offset, variable_chunk)
                    continue
            else:
                reference_hourlies = []

            if len(local_hourlies) != len(point_chunk):
                report["failures"].append(
                    {
                        "point_index": point_offset,
                        "points": point_chunk,
                        "variables": variable_chunk,
                        "reason": "local_point_count_mismatch",
                        "expected": len(point_chunk),
                        "actual": len(local_hourlies),
                    }
                )
                finish_chunk(point_offset, variable_chunk)
                continue
            if reference_base_url and len(reference_hourlies) != len(point_chunk):
                report["failures"].append(
                    {
                        "point_index": point_offset,
                        "points": point_chunk,
                        "variables": variable_chunk,
                        "reason": "reference_point_count_mismatch",
                        "expected": len(point_chunk),
                        "actual": len(reference_hourlies),
                    }
                )
                finish_chunk(point_offset, variable_chunk)
                continue

            for point_index, point in enumerate(point_chunk, start=point_offset):
                local_hourly = local_hourlies[point_index - point_offset]
                reference_hourly = reference_hourlies[point_index - point_offset] if reference_base_url else None
                if reference_hourly is not None:
                    local_times = list(local_hourly.get("time") or [])[:frames]
                    reference_times = list(reference_hourly.get("time") or [])[:frames]
                    if local_times != reference_times:
                        report["failures"].append(
                            {
                                "point_index": point_index,
                                "point": point,
                                "reason": "time_mismatch",
                                "local_times": local_times[:10],
                                "reference_times": reference_times[:10],
                                "local_frames": len(local_times),
                                "reference_frames": len(reference_times),
                            }
                        )
                        continue
                for variable in variable_chunk:
                    local_series = local_hourly.get(variable)
                    summary = summarize_variable(local_series, frames=frames)
                    report["checked_values"] += frames

                    if reference_hourly is None:
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

            finish_chunk(point_offset, variable_chunk)

    report["elapsed_seconds"] = round(time.time() - started, 3)
    report["passed"] = not report["failures"]
    report.pop("incomplete", None)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate local Open-Meteo GFS/CAMS point API output coverage.")
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--reference-base-url")
    parser.add_argument("--scope", choices=["gfs", "cams"], required=True)
    parser.add_argument("--points", type=int, required=True)
    parser.add_argument("--frames", type=int, default=50)
    parser.add_argument("--start-hour", help="UTC ISO hour used as the first validation frame.")
    parser.add_argument("--end-hour", help="UTC ISO hour used as the last validation frame.")
    parser.add_argument("--chunk-size", type=int, default=30)
    parser.add_argument("--point-chunk-size", type=int, default=10)
    parser.add_argument("--tolerance", type=float, default=0.001)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--request-retries", type=int, default=3)
    parser.add_argument("--request-retry-delay", type=float, default=2.0)
    parser.add_argument("--request-pause", type=float, default=0.0)
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--variables", help="Comma-separated override variable list.")
    parser.add_argument("--allow-all-null", action="store_true")
    parser.add_argument("--progress-report", help="Write an incremental progress report after each request chunk.")
    parser.add_argument("--left-lon", type=float, default=70.0)
    parser.add_argument("--right-lon", type=float, default=140.0)
    parser.add_argument("--bottom-lat", type=float, default=0.0)
    parser.add_argument("--top-lat", type=float, default=58.0)
    parser.add_argument("--output-report", required=True)
    return parser.parse_args()


def parse_utc_hour(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.replace(minute=0, second=0, microsecond=0)


def format_utc_hour(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")


def default_start_hour(now: datetime | None = None) -> datetime:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


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
    start_dt = parse_utc_hour(args.start_hour) if args.start_hour else default_start_hour()
    end_dt = parse_utc_hour(args.end_hour) if args.end_hour else start_dt + timedelta(hours=args.frames - 1)
    start_hour = format_utc_hour(start_dt)
    end_hour = format_utc_hour(end_dt)
    report = validate_scope(
        api_base_url=args.api_base_url,
        reference_base_url=args.reference_base_url,
        scope=args.scope,
        variables=variables,
        points=points,
        frames=args.frames,
        chunk_size=args.chunk_size,
        point_chunk_size=args.point_chunk_size,
        start_hour=start_hour,
        end_hour=end_hour,
        tolerance=args.tolerance,
        timeout=args.timeout,
        allow_all_null=args.allow_all_null,
        request_retries=args.request_retries,
        request_retry_delay=args.request_retry_delay,
        request_pause=args.request_pause,
        progress_path=Path(args.progress_report) if args.progress_report else None,
    )
    output_path = Path(args.output_report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "failures": len(report["failures"]), "report": str(output_path)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
