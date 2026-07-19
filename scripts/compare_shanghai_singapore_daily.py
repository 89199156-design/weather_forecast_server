#!/usr/bin/env python3
"""Strictly compare three days of Shanghai/Singapore daily API output.

Every requested GFS and CAMS daily variable, unit, date, null and numeric JSON
type participates in the acceptance gate. Only request execution time is
excluded by the shared canonical JSON comparator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from compare_shanghai_singapore_api import (
    canonical_bytes,
    chunks,
    comparison_start,
    fetch,
    random_points,
    strictly_equal,
    validate_run_identity_report,
)


GFS_DAILY = (
    "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
    "apparent_temperature_max", "apparent_temperature_min", "apparent_temperature_mean",
    "precipitation_sum", "rain_sum", "showers_sum", "snowfall_sum",
    "snowfall_water_equivalent_sum", "weather_code", "weathercode",
    "wind_speed_10m_max", "wind_speed_10m_min", "wind_speed_10m_mean",
    "windspeed_10m_max", "windspeed_10m_min", "windspeed_10m_mean",
    "wind_gusts_10m_max", "wind_gusts_10m_min", "wind_gusts_10m_mean",
    "windgusts_10m_max", "windgusts_10m_min", "windgusts_10m_mean",
    "wind_direction_10m_dominant", "winddirection_10m_dominant",
    "precipitation_hours",
    "visibility_max", "visibility_min", "visibility_mean",
    "pressure_msl_max", "pressure_msl_min", "pressure_msl_mean",
    "surface_pressure_max", "surface_pressure_min", "surface_pressure_mean",
    "cloud_cover_max", "cloud_cover_min", "cloud_cover_mean",
    "cloudcover_max", "cloudcover_min", "cloudcover_mean",
    "dew_point_2m_max", "dew_point_2m_min", "dew_point_2m_mean",
    "dewpoint_2m_max", "dewpoint_2m_min", "dewpoint_2m_mean",
    "relative_humidity_2m_max", "relative_humidity_2m_min", "relative_humidity_2m_mean",
    "snow_depth_max", "snow_depth_min", "snow_depth_mean",
    "uv_index_max", "uv_index_clear_sky_max",
)

CAMS_DAILY = (
    "chinese_aqi", "chinese_aqi_pm2_5", "chinese_aqi_pm10",
    "chinese_aqi_no2", "chinese_aqi_nitrogen_dioxide",
    "chinese_aqi_o3", "chinese_aqi_ozone",
    "chinese_aqi_so2", "chinese_aqi_sulphur_dioxide",
    "chinese_aqi_co", "chinese_aqi_carbon_monoxide",
)
CAMS_DAILY_STRICT = frozenset(CAMS_DAILY)


def daily_variables_for_scope(scope: str) -> list[str]:
    if scope == "gfs":
        return list(GFS_DAILY)
    if scope == "cams":
        return list(CAMS_DAILY)
    raise ValueError(f"unsupported scope: {scope}")


def daily_start_date(gfs_run: str):
    return (comparison_start("gfs", gfs_run) + timedelta(hours=8)).date()


def parse_utc_hour(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 UTC hour")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    if parsed.minute != 0 or parsed.second != 0 or parsed.microsecond != 0:
        raise ValueError(f"{field} must be aligned to an exact UTC hour")
    return parsed


def derive_complete_daily_window(
    shared_start: datetime,
    shared_end: datetime,
    days: int,
) -> tuple[date, date]:
    """Return complete Asia/Shanghai dates contained in an inclusive UTC window."""
    if days <= 0:
        raise ValueError("days must be positive")
    if shared_start > shared_end:
        raise ValueError("shared GFS window start is after its end")
    local_start = shared_start + timedelta(hours=8)
    first_date = local_start.date()
    if local_start.time() != datetime.min.time():
        first_date += timedelta(days=1)
    last_date = first_date + timedelta(days=days - 1)
    required_start = datetime.combine(first_date, datetime.min.time()) - timedelta(hours=8)
    required_end = required_start + timedelta(days=days) - timedelta(hours=1)
    if shared_start > required_start or shared_end < required_end:
        raise ValueError(
            f"shared GFS window {shared_start:%Y-%m-%dT%H:00}.."
            f"{shared_end:%Y-%m-%dT%H:00} cannot cover {days} complete "
            f"Asia/Shanghai days {first_date.isoformat()}..{last_date.isoformat()}"
        )
    return first_date, last_date


def load_daily_window_from_hourly_report(
    path: Path,
    gfs_run: str,
    days: int,
) -> tuple[date, date, dict[str, Any]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("hourly acceptance report must be a JSON object")
    if report.get("passed") is not True:
        raise ValueError("hourly acceptance report passed must be true")
    if report.get("gfs_run") != gfs_run:
        raise ValueError(
            f"hourly acceptance report GFS run {report.get('gfs_run')} != {gfs_run}"
        )
    window = report.get("shared_gfs_window")
    if not isinstance(window, dict):
        raise ValueError("hourly acceptance report is missing shared_gfs_window")
    if window.get("reason") != "actual_shared_window":
        raise ValueError("shared_gfs_window reason must be actual_shared_window")
    if window.get("run") != gfs_run:
        raise ValueError(f"shared_gfs_window run {window.get('run')} != {gfs_run}")
    shared_start = parse_utc_hour(window.get("shared_start"), "shared_gfs_window.shared_start")
    shared_end = parse_utc_hour(window.get("shared_end"), "shared_gfs_window.shared_end")
    actual_hours = int((shared_end - shared_start).total_seconds() // 3600) + 1
    if actual_hours <= 0:
        raise ValueError("shared GFS window must contain at least one hour")
    reported_hours = window.get("shared_hours")
    if isinstance(reported_hours, bool) or not isinstance(reported_hours, int):
        raise ValueError("shared_gfs_window.shared_hours must be an integer")
    if reported_hours != actual_hours:
        raise ValueError(
            f"shared_gfs_window.shared_hours {reported_hours} != inclusive range {actual_hours}"
        )
    start, end = derive_complete_daily_window(shared_start, shared_end, days)
    source = {
        "type": "hourly_acceptance_report_shared_gfs_window",
        "report": str(path.resolve()),
        "field": "shared_gfs_window",
        "reason": window["reason"],
        "shared_start": window["shared_start"],
        "shared_end": window["shared_end"],
        "shared_hours": reported_hours,
    }
    return start, end, source


def request_path(
    scope: str,
    points: list[dict[str, float]],
    variables: list[str],
    gfs_run: str,
    days: int,
    start_date: date | None = None,
) -> str:
    start = start_date or daily_start_date(gfs_run)
    end = start + timedelta(days=days - 1)
    params = {
        "latitude": ",".join(str(point["latitude"]) for point in points),
        "longitude": ",".join(str(point["longitude"]) for point in points),
        "daily": ",".join(variables),
        "timezone": "Asia/Shanghai",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    if scope == "gfs":
        params.update({"models": "gfs_global", "wind_speed_unit": "ms"})
        endpoint = "/v1/forecast"
    elif scope == "cams":
        params["domains"] = "cams_global"
        endpoint = "/v1/air-quality"
    else:
        raise ValueError(f"unsupported scope: {scope}")
    return endpoint + "?" + urllib.parse.urlencode(params)


def validate_daily_payload(
    payload: Any,
    point_count: int,
    variables: list[str],
    gfs_run: str,
    days: int,
    start_date: date | None = None,
) -> None:
    responses = payload if isinstance(payload, list) else [payload]
    if len(responses) != point_count:
        raise ValueError(f"response point count {len(responses)} != {point_count}")
    start = start_date or daily_start_date(gfs_run)
    expected_dates = [(start + timedelta(days=index)).isoformat() for index in range(days)]
    for index, response in enumerate(responses):
        daily = response.get("daily")
        if not isinstance(daily, dict) or daily.get("time") != expected_dates:
            raise ValueError(f"point {index} does not contain the exact {days}-day window")
        missing = [variable for variable in variables if variable not in daily]
        if missing:
            raise ValueError(f"point {index} missing daily variables: {','.join(missing[:5])}")
        bad_lengths = [
            variable for variable in variables
            if not isinstance(daily[variable], list) or len(daily[variable]) != days
        ]
        if bad_lengths:
            raise ValueError(f"point {index} invalid daily series: {','.join(bad_lengths[:5])}")


def daily_mismatch_summary(
    shanghai: Any,
    singapore: Any,
    variables: list[str],
    max_examples_per_field: int = 2,
) -> dict[str, Any]:
    left_responses = shanghai if isinstance(shanghai, list) else [shanghai]
    right_responses = singapore if isinstance(singapore, list) else [singapore]
    counts: dict[str, dict[str, int]] = {"metadata": {}, "daily_units": {}, "daily_values": {}}
    examples: list[dict[str, Any]] = []
    example_counts: dict[tuple[str, str], int] = {}

    def record(category: str, field: str, point: int, day: int | None, left: Any, right: Any) -> None:
        counts[category][field] = counts[category].get(field, 0) + 1
        key = (category, field)
        if example_counts.get(key, 0) < max_examples_per_field:
            example = {"category": category, "field": field, "point": point,
                       "shanghai": left, "singapore": right}
            if day is not None:
                example["day"] = day
            examples.append(example)
            example_counts[key] = example_counts.get(key, 0) + 1

    excluded = {"generationtime_ms", "daily", "daily_units"}
    for point, (left_response, right_response) in enumerate(zip(left_responses, right_responses)):
        for field in sorted((set(left_response) | set(right_response)) - excluded):
            left = left_response.get(field)
            right = right_response.get(field)
            if not strictly_equal(left, right):
                record("metadata", field, point, None, left, right)
        left_units = left_response.get("daily_units", {})
        right_units = right_response.get("daily_units", {})
        for field in sorted(set(left_units) | set(right_units)):
            left = left_units.get(field)
            right = right_units.get(field)
            if not strictly_equal(left, right):
                record("daily_units", field, point, None, left, right)
        left_daily = left_response.get("daily", {})
        right_daily = right_response.get("daily", {})
        for field in ("time", *variables):
            for day, (left, right) in enumerate(zip(left_daily.get(field, []), right_daily.get(field, []))):
                if not strictly_equal(left, right):
                    record("daily_values", field, point, day, left, right)
    return {"counts": counts, "examples": examples}


def compare_job(job: dict[str, Any], shanghai_url: str, singapore_url: str, timeout: float,
                request_pause: float) -> dict[str, Any]:
    try:
        path = request_path(
            job["scope"], job["points"], job["variables"], job["gfs_run"], job["days"],
            job.get("daily_start"),
        )
        shanghai = fetch(shanghai_url, path, timeout)
        singapore = fetch(singapore_url, path, timeout)
        validate_daily_payload(
            shanghai, len(job["points"]), job["variables"], job["gfs_run"], job["days"],
            job.get("daily_start"),
        )
        validate_daily_payload(
            singapore, len(job["points"]), job["variables"], job["gfs_run"], job["days"],
            job.get("daily_start"),
        )
        strict_variables = list(job["variables"])
        left = canonical_bytes(shanghai)
        right = canonical_bytes(singapore)
        result = {
            "job_id": job["job_id"], "scope": job["scope"], "equal": left == right,
            "shanghai_sha256": hashlib.sha256(left).hexdigest(),
            "singapore_sha256": hashlib.sha256(right).hexdigest(),
            "points": len(job["points"]), "variables": len(job["variables"]),
            "strict_variables": len(strict_variables),
            "values": len(job["points"]) * len(strict_variables) * job["days"],
        }
        if left != right:
            result["field_mismatches"] = daily_mismatch_summary(
                shanghai, singapore, strict_variables
            )
        return result
    finally:
        if request_pause > 0:
            time.sleep(request_pause)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shanghai-url", required=True)
    parser.add_argument("--singapore-url", required=True)
    parser.add_argument("--gfs-run", required=True)
    parser.add_argument("--cams-run", required=True)
    parser.add_argument("--run-identity-report", required=True)
    parser.add_argument(
        "--hourly-acceptance-report", "--hourly-window-report",
        dest="hourly_acceptance_report", required=True,
        help="hourly acceptance report containing the actual shared_gfs_window",
    )
    parser.add_argument("--identity-max-age-seconds", type=float, default=900.0)
    parser.add_argument("--output-report", required=True)
    parser.add_argument("--point-count", type=int, default=2000)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--point-batch-size", type=int, default=50)
    parser.add_argument("--variable-batch-size", type=int, default=64)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--request-pause", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--bounds", nargs=4, type=float, default=(70.0, 140.0, 0.0, 58.0))
    parser.add_argument("--allow-reduced-test", action="store_true")
    args = parser.parse_args()
    if not args.allow_reduced_test and (args.point_count != 2000 or args.days != 3):
        parser.error("acceptance runs require exactly 2000 points and 3 days")
    if args.days <= 0 or args.workers <= 0 or args.request_pause < 0:
        parser.error("days/workers must be positive and request pause non-negative")
    try:
        validate_run_identity_report(
            Path(args.run_identity_report),
            args.gfs_run,
            args.cams_run,
            args.identity_max_age_seconds,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    try:
        daily_start, daily_end, daily_window_source = load_daily_window_from_hourly_report(
            Path(args.hourly_acceptance_report), args.gfs_run, args.days
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    points = random_points(args.point_count, args.seed, tuple(args.bounds))
    jobs: list[dict[str, Any]] = []
    for scope in ("gfs", "cams"):
        for point_index, point_group in enumerate(chunks(points, args.point_batch_size)):
            for variable_index, variable_group in enumerate(
                chunks(daily_variables_for_scope(scope), args.variable_batch_size)
            ):
                jobs.append({
                    "job_id": f"{scope}-p{point_index:04d}-v{variable_index:03d}",
                    "scope": scope, "points": point_group, "variables": variable_group,
                    "gfs_run": args.gfs_run, "days": args.days,
                    "daily_start": daily_start,
                })

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(compare_job, job, args.shanghai_url, args.singapore_url,
                            args.timeout, args.request_pause): job
            for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append({"job_id": job["job_id"], "error": f"{type(exc).__name__}: {exc}"})

    results.sort(key=lambda item: item["job_id"])
    mismatches = [item for item in results if not item["equal"]]
    passed = not errors and not mismatches and len(results) == len(jobs)
    report = {
        "passed": passed, "strict_equality": True, "excluded_fields": ["generationtime_ms"],
        "strict_equality_scope": (
            "all requested GFS and CAMS daily aggregations; "
            "all response metadata, units and date axes"
        ),
        "excluded_value_semantics": [],
        "point_count": args.point_count, "days": args.days, "workers": args.workers,
        "seed": args.seed, "bounds": list(args.bounds),
        "point_sha256": hashlib.sha256(canonical_bytes(points)).hexdigest(),
        "gfs_run": args.gfs_run, "cams_run": args.cams_run,
        "run_identity_report": str(Path(args.run_identity_report).resolve()),
        "hourly_acceptance_report": str(Path(args.hourly_acceptance_report).resolve()),
        "daily_start": daily_start.isoformat(), "daily_end": daily_end.isoformat(),
        "daily_window_source": daily_window_source,
        "gfs_daily_variable_count": len(GFS_DAILY),
        "gfs_daily_variables": list(GFS_DAILY),
        "cams_daily_variable_count": len(CAMS_DAILY),
        "cams_daily_variables": list(CAMS_DAILY),
        "cams_daily_strict_variables": sorted(CAMS_DAILY_STRICT),
        "jobs_expected": len(jobs), "jobs_completed": len(results),
        "values_compared": sum(item["values"] for item in results),
        "mismatches": mismatches[:100], "errors": errors[:100], "job_digests": results,
    }
    output = Path(args.output_report)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({key: report[key] for key in
                      ("passed", "point_count", "days", "jobs_completed", "values_compared")},
                     ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
