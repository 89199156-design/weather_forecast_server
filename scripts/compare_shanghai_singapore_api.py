#!/usr/bin/env python3
"""Strictly compare Shanghai and Singapore API output for 2,000 regional points.

Only ``generationtime_ms`` is excluded because it measures request execution
time. All other JSON fields, units, timestamps, nulls, numeric JSON types and
comparable values must be byte-identical after canonical JSON serialization.
Acceptance runs cover the complete window actually shared by both GFS APIs,
bounded by the nominal publication window through f384, and CAMS through
f120. CAMS fields backed by the three-hourly direct source are
compared only at f000, f003, ..., f120; Shanghai interpolation-only hours are
outside this project's acceptance semantics. ``--hours`` exists only for
explicitly reduced diagnostic runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PRESSURE_LEVELS = (1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150, 100, 50)
GFS_PUBLIC_SURFACE = (
    "temperature_2m", "cloud_cover", "cloud_cover_low", "cloud_cover_mid",
    "cloud_cover_high", "pressure_msl", "relative_humidity_2m",
    "precipitation", "snow_depth", "showers", "snowfall_water_equivalent",
    "uv_index", "uv_index_clear_sky", "wind_gusts_10m", "cape", "visibility",
    "dew_point_2m", "apparent_temperature", "surface_pressure", "weather_code", "rain", "snowfall",
    "wind_speed_10m", "wind_direction_10m",
)
GFS_SINGLE_BATCH_F000_MISSING = frozenset({
    "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
    "precipitation", "showers", "snowfall_water_equivalent",
    "uv_index", "uv_index_clear_sky",
    # These derived fields require at least one of the missing official f000
    # inputs above. Singapore intentionally does not retain the previous
    # cycle's f006 as a cross-batch fallback.
    "apparent_temperature", "weather_code", "rain", "snowfall",
})
GFS_SINGLE_BATCH_F000_REASON = (
    "shanghai_may_fill_official_f000_source_gaps_from_previous_cycle_f006_"
    "while_singapore_keeps_strict_f000_to_f005_history_batches"
)
GFS_PRESSURE_FAMILIES = (
    "temperature", "relative_humidity", "dew_point", "cloud_cover",
    "wind_speed", "wind_direction", "geopotential_height", "vertical_velocity",
)
CAMS_DIRECT = (
    "pm2_5", "pm10", "aerosol_optical_depth", "dust", "carbon_monoxide",
    "nitrogen_dioxide", "ozone", "sulphur_dioxide",
)
CAMS_DERIVED = (
    "chinese_aqi", "chinese_aqi_pm2_5", "chinese_aqi_pm10", "chinese_aqi_no2",
    "chinese_aqi_o3", "chinese_aqi_so2", "chinese_aqi_co",
    "chinese_aqi_nitrogen_dioxide", "chinese_aqi_ozone",
    "chinese_aqi_sulphur_dioxide", "chinese_aqi_carbon_monoxide",
)

# CAMS_GLOBAL contains two source cadences. Surface fields are disseminated
# hourly, while the ml137/additional fields (and the greenhouse CO input) have
# direct values every three hours. Derived fields inherit the cadence of all
# inputs they require: a derived field is three-hourly-comparable whenever any
# dependency is backed by a three-hourly source.
CAMS_HOURLY_DIRECT_SOURCE = frozenset({
    "pm2_5", "pm10", "aerosol_optical_depth",
    "chinese_aqi_pm2_5", "chinese_aqi_pm10",
})
CAMS_THREE_HOURLY_DIRECT_SOURCE = frozenset({
    "dust", "carbon_monoxide", "nitrogen_dioxide", "ozone", "sulphur_dioxide",
    "chinese_aqi", "chinese_aqi_no2", "chinese_aqi_o3", "chinese_aqi_so2",
    "chinese_aqi_co", "chinese_aqi_nitrogen_dioxide", "chinese_aqi_ozone",
    "chinese_aqi_sulphur_dioxide", "chinese_aqi_carbon_monoxide",
})
CAMS_EXPECTED_SEMANTIC_DIFFERENCE_VARIABLES = frozenset()
CAMS_ROLLING_WINDOW_WAIVER_REASON = (
    "rolling_window_depends_on_shanghai_interpolated_ml137_hours_while_"
    "singapore_uses_direct_hourly_source_values"
)

_CAMS_VARIABLE_CONTRACT = frozenset((*CAMS_DIRECT, *CAMS_DERIVED))
if (
    CAMS_HOURLY_DIRECT_SOURCE & CAMS_THREE_HOURLY_DIRECT_SOURCE
    or (CAMS_HOURLY_DIRECT_SOURCE | CAMS_THREE_HOURLY_DIRECT_SOURCE)
    != _CAMS_VARIABLE_CONTRACT
    or not CAMS_EXPECTED_SEMANTIC_DIFFERENCE_VARIABLES <= CAMS_THREE_HOURLY_DIRECT_SOURCE
):
    raise RuntimeError("every CAMS variable must have one explicit source/waiver semantic")


def variables_for_scope(scope: str) -> list[str]:
    if scope == "gfs":
        pressure = [f"{family}_{level}hPa" for family in GFS_PRESSURE_FAMILIES for level in PRESSURE_LEVELS]
        return list(dict.fromkeys((*GFS_PUBLIC_SURFACE, *pressure)))
    if scope == "cams":
        return list(dict.fromkeys((*CAMS_DIRECT, *CAMS_DERIVED)))
    raise ValueError(f"unsupported scope: {scope}")


def direct_source_cadence_hours(scope: str, variable: str) -> int:
    """Return the direct-source cadence used by the parity contract."""
    if scope == "gfs":
        return 1
    if scope != "cams":
        raise ValueError(f"unsupported scope: {scope}")
    if variable in CAMS_HOURLY_DIRECT_SOURCE:
        return 1
    if variable in CAMS_THREE_HOURLY_DIRECT_SOURCE:
        return 3
    raise ValueError(f"CAMS variable has no direct-source cadence contract: {variable}")


def direct_source_hour_indices(
    scope: str,
    variable: str,
    run: str,
    hours: int,
    *,
    start: datetime | None = None,
) -> list[int]:
    """Return response indexes that represent direct (not interpolated) input."""
    if hours <= 0:
        raise ValueError("hours must be positive")
    cadence = direct_source_cadence_hours(scope, variable)
    if cadence == 1:
        return list(range(hours))

    source_run = parse_run(run)
    start = start or comparison_start(scope, run)
    offset_seconds = int((start - source_run).total_seconds())
    if offset_seconds % 3600 != 0:
        raise ValueError("comparison window is not aligned to an exact source hour")
    offset_hours = offset_seconds // 3600
    return [index for index in range(hours) if (offset_hours + index) % cadence == 0]


def strict_comparison_hour_indices(
    scope: str,
    variable: str,
    run: str,
    hours: int,
    *,
    start: datetime | None = None,
) -> list[int]:
    if scope == "cams" and variable in CAMS_EXPECTED_SEMANTIC_DIFFERENCE_VARIABLES:
        return []
    return direct_source_hour_indices(scope, variable, run, hours, start=start)


def expected_semantic_difference_summary(
    shanghai: Any,
    singapore: Any,
    variables: list[str],
    hours: int,
    max_examples_per_field: int = 2,
) -> dict[str, Any]:
    """Observe waived values without allowing them to affect the strict gate."""
    left_responses = shanghai if isinstance(shanghai, list) else [shanghai]
    right_responses = singapore if isinstance(singapore, list) else [singapore]
    by_variable: dict[str, dict[str, int]] = {
        variable: {"values_observed": 0, "equal_values": 0, "mismatched_values": 0}
        for variable in variables
    }
    examples: list[dict[str, Any]] = []
    example_counts: dict[str, int] = {}
    for point, (left_response, right_response) in enumerate(zip(left_responses, right_responses)):
        left_hourly = left_response.get("hourly", {})
        right_hourly = right_response.get("hourly", {})
        for variable in variables:
            left_values = left_hourly.get(variable, [])
            right_values = right_hourly.get(variable, [])
            counts = by_variable[variable]
            for hour in range(hours):
                left = left_values[hour]
                right = right_values[hour]
                counts["values_observed"] += 1
                if strictly_equal(left, right):
                    counts["equal_values"] += 1
                    continue
                counts["mismatched_values"] += 1
                if example_counts.get(variable, 0) < max_examples_per_field:
                    examples.append({
                        "variable": variable,
                        "point": point,
                        "hour": hour,
                        "shanghai": left,
                        "singapore": right,
                    })
                    example_counts[variable] = example_counts.get(variable, 0) + 1
    return {
        "reason": CAMS_ROLLING_WINDOW_WAIVER_REASON,
        "variables": variables,
        "values_observed": sum(item["values_observed"] for item in by_variable.values()),
        "equal_values": sum(item["equal_values"] for item in by_variable.values()),
        "mismatched_values": sum(item["mismatched_values"] for item in by_variable.values()),
        "by_variable": by_variable,
        "examples": examples,
    }


def comparable_payload(
    payload: Any,
    variables: list[str],
    hour_indices_by_variable: dict[str, list[int]],
    normalized_null_positions: set[tuple[int, str, int]] | None = None,
) -> Any:
    """Keep full API structure while filtering only explicitly excluded values."""
    responses = payload if isinstance(payload, list) else [payload]
    filtered_responses: list[Any] = []
    normalized_null_positions = normalized_null_positions or set()
    for point, response in enumerate(responses):
        if not isinstance(response, dict):
            filtered_responses.append(response)
            continue
        filtered = dict(response)
        hourly = response.get("hourly")
        if isinstance(hourly, dict):
            filtered_hourly = dict(hourly)
            for variable in variables:
                values = hourly.get(variable)
                if not isinstance(values, list):
                    continue
                filtered_hourly[variable] = [
                    None
                    if (point, variable, index) in normalized_null_positions
                    else values[index]
                    for index in hour_indices_by_variable[variable]
                ]
            filtered["hourly"] = filtered_hourly
        filtered_responses.append(filtered)
    return filtered_responses if isinstance(payload, list) else filtered_responses[0]


def gfs_single_batch_boundary_difference_summary(
    shanghai: Any,
    singapore: Any,
    variables: list[str],
    run: str,
    start: datetime,
    hours: int,
    max_examples_per_field: int = 2,
) -> tuple[set[tuple[int, str, int]], dict[str, Any]]:
    """Allow only Shanghai value / Singapore null at retained GFS f000."""
    latest = parse_run(run)
    source_references = {
        latest - timedelta(hours=6 * offset) for offset in range(5)
    }
    boundary_indices = {
        index
        for index in range(hours)
        if start + timedelta(hours=index) in source_references
    }
    left_responses = shanghai if isinstance(shanghai, list) else [shanghai]
    right_responses = singapore if isinstance(singapore, list) else [singapore]
    positions: set[tuple[int, str, int]] = set()
    by_variable: dict[str, int] = {}
    examples: list[dict[str, Any]] = []
    example_counts: dict[str, int] = {}
    for point, (left_response, right_response) in enumerate(
        zip(left_responses, right_responses)
    ):
        left_hourly = left_response.get("hourly", {})
        right_hourly = right_response.get("hourly", {})
        for variable in variables:
            if variable not in GFS_SINGLE_BATCH_F000_MISSING:
                continue
            left_values = left_hourly.get(variable, [])
            right_values = right_hourly.get(variable, [])
            for hour in boundary_indices:
                left = left_values[hour]
                right = right_values[hour]
                if left is None or right is not None:
                    continue
                positions.add((point, variable, hour))
                by_variable[variable] = by_variable.get(variable, 0) + 1
                if example_counts.get(variable, 0) < max_examples_per_field:
                    examples.append({
                        "variable": variable,
                        "point": point,
                        "hour": hour,
                        "time": format_hour(start + timedelta(hours=hour)),
                        "shanghai": left,
                        "singapore": right,
                    })
                    example_counts[variable] = example_counts.get(variable, 0) + 1
    return positions, {
        "reason": GFS_SINGLE_BATCH_F000_REASON,
        "allowed_direction": "shanghai_non_null_singapore_null_only",
        "source_run_references": sorted(format_hour(value) for value in source_references),
        "values_excluded": len(positions),
        "by_variable": by_variable,
        "examples": examples,
    }


def random_points(count: int, seed: int, bounds: tuple[float, float, float, float]) -> list[dict[str, float]]:
    left, right, bottom, top = bounds
    if count <= 0 or not left < right or not bottom < top:
        raise ValueError("invalid point count or bounds")
    rng = random.Random(seed)
    points: list[dict[str, float]] = []
    seen: set[tuple[float, float]] = set()
    while len(points) < count:
        point = (round(rng.uniform(bottom, top), 6), round(rng.uniform(left, right), 6))
        if point in seen:
            continue
        seen.add(point)
        points.append({"latitude": point[0], "longitude": point[1]})
    return points


def chunks(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        raise ValueError("chunk size must be positive")
    return [items[index:index + size] for index in range(0, len(items), size)]


def parse_run(value: str) -> datetime:
    if not re.fullmatch(r"\d{10}", value):
        raise ValueError(f"run must use YYYYMMDDHH: {value}")
    return datetime.strptime(value, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def comparison_start(scope: str, run: str) -> datetime:
    parsed = parse_run(run)
    if scope == "gfs":
        # Shanghai publishes GFS from 00:00 of the UTC+8 local day that
        # contains the completed full-horizon publication. The operational
        # full run is normally selected about four hours after initialization.
        local = parsed + timedelta(hours=4 + 8)
        return local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8)
    if scope == "cams":
        return parsed
    raise ValueError(f"unsupported scope: {scope}")


def full_hours_for_scope(scope: str, run: str) -> int:
    parsed = parse_run(run)
    start = comparison_start(scope, run)
    if scope == "gfs":
        end = parsed + timedelta(hours=384)
    elif scope == "cams":
        end = parsed + timedelta(hours=120)
    else:
        raise ValueError(f"unsupported scope: {scope}")
    hours = int((end - start).total_seconds() // 3600) + 1
    if hours <= 0:
        raise ValueError(f"invalid complete time window for {scope} run {run}")
    return hours


def format_hour(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:00")


def request_path(
    scope: str,
    points: list[dict[str, float]],
    variables: list[str],
    run: str,
    hours: int,
    *,
    start: datetime | None = None,
) -> str:
    start = start or comparison_start(scope, run)
    end = start + timedelta(hours=hours - 1)
    params = {
        "latitude": ",".join(str(point["latitude"]) for point in points),
        "longitude": ",".join(str(point["longitude"]) for point in points),
        "hourly": ",".join(variables),
        "timezone": "UTC",
        "timeformat": "iso8601",
        "start_hour": format_hour(start),
        "end_hour": format_hour(end),
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


def fetch(base_url: str, path: str, timeout: float) -> Any:
    request = urllib.request.Request(base_url.rstrip("/") + path, headers={"Accept": "application/json", "Cache-Control": "no-cache"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_hour_axis(payload: Any, endpoint: str) -> list[datetime]:
    responses = payload if isinstance(payload, list) else [payload]
    if len(responses) != 1 or not isinstance(responses[0], dict):
        raise ValueError(f"{endpoint} GFS availability probe did not return one point")
    hourly = responses[0].get("hourly")
    times = hourly.get("time") if isinstance(hourly, dict) else None
    values = hourly.get("temperature_2m") if isinstance(hourly, dict) else None
    if not isinstance(times, list) or not times:
        raise ValueError(f"{endpoint} GFS availability probe has no hourly time axis")
    if not isinstance(values, list) or len(values) != len(times):
        raise ValueError(
            f"{endpoint} GFS availability probe has an invalid temperature_2m series"
        )

    parsed: list[datetime] = []
    for index, value in enumerate(times):
        if not isinstance(value, str):
            raise ValueError(f"{endpoint} GFS probe hour {index} is not a string")
        try:
            timestamp = datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(
                tzinfo=timezone.utc
            )
        except ValueError as exc:
            raise ValueError(
                f"{endpoint} GFS probe hour {index} is not an exact UTC hour: {value}"
            ) from exc
        if timestamp.minute != 0:
            raise ValueError(
                f"{endpoint} GFS probe hour {index} is not aligned to an exact hour"
            )
        if parsed and timestamp != parsed[-1] + timedelta(hours=1):
            raise ValueError(
                f"{endpoint} GFS availability axis is not strictly contiguous hourly"
            )
        parsed.append(timestamp)
    return parsed


def discover_shared_gfs_window(
    shanghai_url: str,
    singapore_url: str,
    point: dict[str, float],
    run: str,
    timeout: float,
) -> dict[str, Any]:
    """Probe both APIs once and return their full contiguous GFS intersection."""
    nominal_start = comparison_start("gfs", run)
    nominal_end = parse_run(run) + timedelta(hours=384)
    nominal_hours = full_hours_for_scope("gfs", run)
    path = request_path(
        "gfs", [point], ["temperature_2m"], run, nominal_hours,
        start=nominal_start,
    )
    axes = {
        "shanghai": parse_hour_axis(fetch(shanghai_url, path, timeout), "shanghai"),
        "singapore": parse_hour_axis(fetch(singapore_url, path, timeout), "singapore"),
    }
    for endpoint, axis in axes.items():
        if axis[0] < nominal_start or axis[-1] > nominal_end:
            raise ValueError(
                f"{endpoint} GFS availability axis escapes the nominal f384 window"
            )

    shared_start = max(axis[0] for axis in axes.values())
    shared_end = min(axis[-1] for axis in axes.values())
    if shared_end < shared_start:
        raise ValueError("Shanghai and Singapore GFS availability windows do not overlap")
    shared_hours = int((shared_end - shared_start).total_seconds() // 3600) + 1
    return {
        "reason": "actual_shared_window",
        "run": run,
        "start_utc": format_hour(shared_start),
        "end_utc": format_hour(shared_end),
        "hours": shared_hours,
        "nominal_start": format_hour(nominal_start),
        "nominal_end": format_hour(nominal_end),
        "shanghai": {
            "start": format_hour(axes["shanghai"][0]),
            "end": format_hour(axes["shanghai"][-1]),
            "hours": len(axes["shanghai"]),
        },
        "singapore": {
            "start": format_hour(axes["singapore"][0]),
            "end": format_hour(axes["singapore"][-1]),
            "hours": len(axes["singapore"]),
        },
        "shared_start": format_hour(shared_start),
        "shared_end": format_hour(shared_end),
        "shared_hours": shared_hours,
    }


def select_gfs_comparison_window(
    shared_window: dict[str, Any],
    requested_hours: int | None,
    *,
    require_acceptance_minimum: bool,
) -> tuple[datetime, int]:
    shared_start = datetime.strptime(
        shared_window["shared_start"], "%Y-%m-%dT%H:%M"
    ).replace(tzinfo=timezone.utc)
    shared_hours = int(shared_window["shared_hours"])
    if require_acceptance_minimum and shared_hours < 300:
        raise ValueError(
            f"complete GFS acceptance requires at least 300 shared hours; got {shared_hours}"
        )
    selected_hours = shared_hours if requested_hours is None else requested_hours
    if selected_hours <= 0 or selected_hours > shared_hours:
        raise ValueError(
            f"requested GFS comparison hours {selected_hours} exceed the "
            f"{shared_hours}-hour shared window"
        )
    return shared_start, selected_hours


def without_dynamic_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [without_dynamic_fields(item) for item in value]
    if isinstance(value, dict):
        return {key: without_dynamic_fields(item) for key, item in value.items() if key != "generationtime_ms"}
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(without_dynamic_fields(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def strictly_equal(left: Any, right: Any) -> bool:
    """Match canonical JSON semantics, including integer/float type differences."""
    return canonical_bytes(left) == canonical_bytes(right)


def field_mismatch_summary(
    shanghai: Any,
    singapore: Any,
    variables: list[str],
    max_examples_per_field: int = 2,
    hour_indices_by_variable: dict[str, list[int]] | None = None,
    ignored_value_positions: set[tuple[int, str, int]] | None = None,
) -> dict[str, Any]:
    left_responses = shanghai if isinstance(shanghai, list) else [shanghai]
    right_responses = singapore if isinstance(singapore, list) else [singapore]
    counts: dict[str, dict[str, int]] = {
        "metadata": {},
        "hourly_units": {},
        "hourly_values": {},
    }
    examples: list[dict[str, Any]] = []
    example_counts: dict[tuple[str, str], int] = {}
    ignored_value_positions = ignored_value_positions or set()

    def record(category: str, field: str, point: int, hour: int | None, left: Any, right: Any) -> None:
        fields = counts[category]
        fields[field] = fields.get(field, 0) + 1
        key = (category, field)
        if example_counts.get(key, 0) < max_examples_per_field:
            example = {
                "category": category,
                "field": field,
                "point": point,
                "shanghai": left,
                "singapore": right,
            }
            if hour is not None:
                example["hour"] = hour
            examples.append(example)
            example_counts[key] = example_counts.get(key, 0) + 1

    excluded_metadata = {"generationtime_ms", "hourly", "hourly_units"}
    for point, (left_response, right_response) in enumerate(zip(left_responses, right_responses)):
        metadata_keys = (set(left_response) | set(right_response)) - excluded_metadata
        for field in sorted(metadata_keys):
            left = left_response.get(field)
            right = right_response.get(field)
            if not strictly_equal(left, right):
                record("metadata", field, point, None, left, right)

        left_units = left_response.get("hourly_units", {})
        right_units = right_response.get("hourly_units", {})
        for field in sorted(set(left_units) | set(right_units)):
            left = left_units.get(field)
            right = right_units.get(field)
            if not strictly_equal(left, right):
                record("hourly_units", field, point, None, left, right)

        left_hourly = left_response.get("hourly", {})
        right_hourly = right_response.get("hourly", {})
        for field in ("time", *variables):
            left_values = left_hourly.get(field, [])
            right_values = right_hourly.get(field, [])
            if field == "time" or hour_indices_by_variable is None:
                indices = range(min(len(left_values), len(right_values)))
            else:
                indices = hour_indices_by_variable[field]
            for hour in indices:
                if (point, field, hour) in ignored_value_positions:
                    continue
                left = left_values[hour]
                right = right_values[hour]
                if not strictly_equal(left, right):
                    record("hourly_values", field, point, hour, left, right)

    return {"counts": counts, "examples": examples}


def validate_payload(
    payload: Any,
    scope: str,
    point_count: int,
    variables: list[str],
    run: str,
    hours: int,
    *,
    start: datetime | None = None,
) -> None:
    responses = payload if isinstance(payload, list) else [payload]
    if len(responses) != point_count:
        raise ValueError(f"response point count {len(responses)} != {point_count}")
    start = start or comparison_start(scope, run)
    expected_times = [(start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:00") for index in range(hours)]
    for index, response in enumerate(responses):
        if not isinstance(response, dict):
            raise ValueError(f"point {index} response is not an object")
        hourly = response.get("hourly")
        if not isinstance(hourly, dict) or hourly.get("time") != expected_times:
            raise ValueError(f"point {index} does not contain the exact {hours}-hour window")
        missing = [variable for variable in variables if variable not in hourly]
        if missing:
            raise ValueError(f"point {index} missing variables: {','.join(missing[:5])}")
        bad_lengths = [variable for variable in variables if not isinstance(hourly[variable], list) or len(hourly[variable]) != hours]
        if bad_lengths:
            raise ValueError(f"point {index} invalid series length: {','.join(bad_lengths[:5])}")


def preflight_public_variable_contracts(
    shanghai_url: str,
    singapore_url: str,
    point: dict[str, float],
    gfs_run: str,
    cams_run: str,
    gfs_start: datetime,
    variable_batch_size: int,
    timeout: float,
) -> int:
    """Reject unsupported public fields before starting the 2,000-point gate."""
    requests = 0
    for scope, run, start in (
        ("gfs", gfs_run, gfs_start),
        ("cams", cams_run, comparison_start("cams", cams_run)),
    ):
        for variables in chunks(variables_for_scope(scope), variable_batch_size):
            path = request_path(scope, [point], variables, run, 1, start=start)
            for endpoint, base_url in (
                ("shanghai", shanghai_url),
                ("singapore", singapore_url),
            ):
                payload = fetch(base_url, path, timeout)
                validate_payload(
                    payload,
                    scope,
                    1,
                    variables,
                    run,
                    1,
                    start=start,
                )
                requests += 1
    return requests


def validate_run_identity_report(
    path: Path,
    gfs_run: str,
    cams_run: str,
    max_age_seconds: float = 900.0,
) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    matched = report.get("matched_latest_runs") or {}
    if report.get("passed") is not True or report.get("same_source_runs") is not True:
        raise ValueError("model run identity report did not pass")
    if report.get("live_snapshot_verified") is not True:
        raise ValueError("model run identity report does not prove the live API snapshots")
    if matched.get("gfs") != gfs_run or matched.get("cams") != cams_run:
        raise ValueError("model run identity report does not match requested GFS/CAMS runs")
    timestamps = [report.get("compared_at"), *((report.get("inventory_collected_at") or {}).values())]
    if len(timestamps) != 3 or any(not isinstance(value, (int, float)) for value in timestamps):
        raise ValueError("model run identity report has no live collection timestamps")
    now = time.time()
    ages = [now - float(value) for value in timestamps]
    if any(age < -300 or age > max_age_seconds for age in ages):
        raise ValueError("model run identity report is stale; collect both current markers again")
    return report


def compare_job_unthrottled(
    job: dict[str, Any],
    shanghai_url: str,
    singapore_url: str,
    timeout: float,
) -> dict[str, Any]:
    start = job.get("start")
    path = request_path(
        job["scope"], job["points"], job["variables"], job["run"], job["hours"],
        start=start,
    )
    shanghai = fetch(shanghai_url, path, timeout)
    singapore = fetch(singapore_url, path, timeout)
    validate_payload(
        shanghai, job["scope"], len(job["points"]), job["variables"],
        job["run"], job["hours"], start=start,
    )
    validate_payload(
        singapore, job["scope"], len(job["points"]), job["variables"],
        job["run"], job["hours"], start=start,
    )
    hour_indices_by_variable = {
        variable: strict_comparison_hour_indices(
            job["scope"], variable, job["run"], job["hours"], start=start
        )
        for variable in job["variables"]
    }
    waived_variables = [
        variable for variable in job["variables"]
        if job["scope"] == "cams"
        and variable in CAMS_EXPECTED_SEMANTIC_DIFFERENCE_VARIABLES
    ]
    single_batch_positions: set[tuple[int, str, int]] = set()
    single_batch_summary = None
    if job["scope"] == "gfs":
        gfs_start = start or comparison_start("gfs", job["run"])
        single_batch_positions, single_batch_summary = (
            gfs_single_batch_boundary_difference_summary(
                shanghai,
                singapore,
                job["variables"],
                job["run"],
                gfs_start,
                job["hours"],
            )
        )
    left = canonical_bytes(comparable_payload(
        shanghai,
        job["variables"],
        hour_indices_by_variable,
        single_batch_positions,
    ))
    right = canonical_bytes(comparable_payload(
        singapore,
        job["variables"],
        hour_indices_by_variable,
        single_batch_positions,
    ))
    values_per_point = sum(len(indices) for indices in hour_indices_by_variable.values())
    excluded_values_per_point = sum(
        job["hours"] - len(direct_source_hour_indices(
            job["scope"], variable, job["run"], job["hours"], start=start
        ))
        for variable in job["variables"]
        if variable not in waived_variables
    )
    semantic_waiver = expected_semantic_difference_summary(
        shanghai, singapore, waived_variables, job["hours"]
    ) if waived_variables else None
    result = {
        "job_id": job["job_id"],
        "scope": job["scope"],
        "equal": left == right,
        "shanghai_sha256": hashlib.sha256(left).hexdigest(),
        "singapore_sha256": hashlib.sha256(right).hexdigest(),
        "points": len(job["points"]),
        "variables": len(job["variables"]),
        "values": len(job["points"]) * values_per_point - len(single_batch_positions),
        "excluded_interpolated_values": len(job["points"]) * excluded_values_per_point,
        "semantic_waiver_values": semantic_waiver["values_observed"] if semantic_waiver else 0,
        "semantic_waiver_mismatches": semantic_waiver["mismatched_values"] if semantic_waiver else 0,
        "gfs_single_batch_boundary_values_excluded": len(single_batch_positions),
    }
    if semantic_waiver is not None:
        result["expected_semantic_differences"] = semantic_waiver
    if single_batch_summary is not None:
        result["gfs_single_batch_boundary_differences"] = single_batch_summary
    if left != right:
        result["field_mismatches"] = field_mismatch_summary(
            shanghai,
            singapore,
            job["variables"],
            hour_indices_by_variable=hour_indices_by_variable,
            ignored_value_positions=single_batch_positions,
        )
    return result


def compare_job(
    job: dict[str, Any],
    shanghai_url: str,
    singapore_url: str,
    timeout: float,
    request_pause: float,
) -> dict[str, Any]:
    try:
        return compare_job_unthrottled(job, shanghai_url, singapore_url, timeout)
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
    parser.add_argument("--identity-max-age-seconds", type=float, default=900.0)
    parser.add_argument("--output-report", required=True)
    parser.add_argument("--point-count", type=int, default=2000)
    parser.add_argument(
        "--hours",
        type=int,
        help="reduced diagnostic hours for both products; forbidden in acceptance mode",
    )
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--point-batch-size", type=int, default=50)
    parser.add_argument("--variable-batch-size", type=int, default=50)
    parser.add_argument("--hour-batch-size", type=int, default=48)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--request-pause", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--bounds", nargs=4, type=float, default=(70.0, 140.0, 0.0, 58.0), metavar=("LEFT", "RIGHT", "BOTTOM", "TOP"))
    parser.add_argument("--allow-reduced-test", action="store_true")
    args = parser.parse_args()
    if not args.allow_reduced_test and (args.point_count != 2000 or args.hours is not None):
        parser.error("acceptance runs require exactly --point-count 2000 and complete model horizons (omit --hours)")
    if args.hours is not None and args.hours <= 0:
        parser.error("--hours must be positive")
    if args.workers <= 0:
        parser.error("--workers must be positive")
    if args.hour_batch_size <= 0:
        parser.error("--hour-batch-size must be positive")
    if args.request_pause < 0:
        parser.error("--request-pause must not be negative")
    try:
        validate_run_identity_report(
            Path(args.run_identity_report),
            args.gfs_run,
            args.cams_run,
            args.identity_max_age_seconds,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    points = random_points(args.point_count, args.seed, tuple(args.bounds))
    try:
        shared_gfs_window = discover_shared_gfs_window(
            args.shanghai_url,
            args.singapore_url,
            points[0],
            args.gfs_run,
            args.timeout,
        )
        gfs_start, gfs_hours = select_gfs_comparison_window(
            shared_gfs_window,
            args.hours,
            require_acceptance_minimum=not args.allow_reduced_test,
        )
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    scope_hours = {
        "gfs": gfs_hours,
        "cams": args.hours or full_hours_for_scope("cams", args.cams_run),
    }
    try:
        preflight_requests = preflight_public_variable_contracts(
            args.shanghai_url,
            args.singapore_url,
            points[0],
            args.gfs_run,
            args.cams_run,
            gfs_start,
            args.variable_batch_size,
            args.timeout,
        )
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        parser.error(f"public variable preflight failed: {exc}")
    jobs: list[dict[str, Any]] = []
    for scope, run in (("gfs", args.gfs_run), ("cams", args.cams_run)):
        parse_run(run)
        scope_start = gfs_start if scope == "gfs" else comparison_start(scope, run)
        for hour_offset in range(0, scope_hours[scope], args.hour_batch_size):
            job_hours = min(args.hour_batch_size, scope_hours[scope] - hour_offset)
            job_start = scope_start + timedelta(hours=hour_offset)
            for point_index, point_group in enumerate(chunks(points, args.point_batch_size)):
                for variable_index, variable_group in enumerate(chunks(variables_for_scope(scope), args.variable_batch_size)):
                    jobs.append({
                        "job_id": (
                            f"{scope}-h{hour_offset:04d}-p{point_index:04d}-"
                            f"v{variable_index:03d}"
                        ),
                        "scope": scope,
                        "run": run,
                        "start": job_start,
                        "hours": job_hours,
                        "points": point_group,
                        "variables": variable_group,
                    })

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                compare_job,
                job,
                args.shanghai_url,
                args.singapore_url,
                args.timeout,
                args.request_pause,
            ): job
            for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - every failed request must be reported.
                errors.append({"job_id": job["job_id"], "error": f"{type(exc).__name__}: {exc}"})

    results.sort(key=lambda item: item["job_id"])
    mismatches = [item for item in results if not item["equal"]]
    field_mismatch_totals: dict[str, dict[str, dict[str, int]]] = {}
    for item in mismatches:
        scope = field_mismatch_totals.setdefault(item["scope"], {})
        details = item.get("field_mismatches", {}).get("counts", {})
        for category, fields in details.items():
            category_totals = scope.setdefault(category, {})
            for field, count in fields.items():
                category_totals[field] = category_totals.get(field, 0) + count
    semantic_waiver_by_variable = {
        variable: {"values_observed": 0, "equal_values": 0, "mismatched_values": 0}
        for variable in sorted(CAMS_EXPECTED_SEMANTIC_DIFFERENCE_VARIABLES)
    }
    semantic_waiver_examples: list[dict[str, Any]] = []
    gfs_single_batch_by_variable: dict[str, int] = {}
    gfs_single_batch_examples: list[dict[str, Any]] = []
    for item in results:
        details = item.get("expected_semantic_differences") or {}
        for variable, counts in (details.get("by_variable") or {}).items():
            totals = semantic_waiver_by_variable[variable]
            for field in ("values_observed", "equal_values", "mismatched_values"):
                totals[field] += int(counts.get(field, 0))
        for example in details.get("examples") or []:
            if len(semantic_waiver_examples) >= 100:
                break
            semantic_waiver_examples.append({"job_id": item["job_id"], **example})
        boundary = item.get("gfs_single_batch_boundary_differences") or {}
        for variable, count in (boundary.get("by_variable") or {}).items():
            gfs_single_batch_by_variable[variable] = (
                gfs_single_batch_by_variable.get(variable, 0) + int(count)
            )
        for example in boundary.get("examples") or []:
            if len(gfs_single_batch_examples) >= 100:
                break
            gfs_single_batch_examples.append({"job_id": item["job_id"], **example})
    point_digest = hashlib.sha256(canonical_bytes(points)).hexdigest()
    cams_cadence_by_variable = {
        variable: direct_source_cadence_hours("cams", variable)
        for variable in variables_for_scope("cams")
    }
    cams_hours_by_cadence = {
        str(cadence): len(direct_source_hour_indices(
            "cams", next(variable for variable, value in cams_cadence_by_variable.items() if value == cadence),
            args.cams_run, scope_hours["cams"],
        ))
        for cadence in sorted(set(cams_cadence_by_variable.values()))
    }
    passed = not errors and not mismatches and len(results) == len(jobs)
    report = {
        "passed": passed,
        "strict_equality": True,
        "strict_equality_scope": (
            "all GFS values; hourly-source CAMS values; direct f=3n CAMS sparse values; "
            "all response metadata, units and time axes"
        ),
        "excluded_fields": ["generationtime_ms"],
        "excluded_value_semantics": [
            "cams_interpolation_only_hours",
            "cams_expected_semantic_difference_waivers",
            "gfs_single_batch_f000_history_fallback",
        ],
        "point_count": args.point_count,
        "gfs_hours": scope_hours["gfs"],
        "cams_hours": scope_hours["cams"],
        "shared_gfs_window": shared_gfs_window,
        "public_variable_preflight_requests": preflight_requests,
        "workers": args.workers,
        "hour_batch_size": args.hour_batch_size,
        "request_pause": args.request_pause,
        "seed": args.seed,
        "bounds": list(args.bounds),
        "point_sha256": point_digest,
        "gfs_run": args.gfs_run,
        "cams_run": args.cams_run,
        "run_identity_report": str(Path(args.run_identity_report).resolve()),
        "gfs_variable_count": len(variables_for_scope("gfs")),
        "cams_variable_count": len(variables_for_scope("cams")),
        "cams_direct_source_cadence_hours": cams_cadence_by_variable,
        "cams_direct_hour_count_by_cadence": cams_hours_by_cadence,
        "cams_expected_semantic_difference_variables": sorted(
            CAMS_EXPECTED_SEMANTIC_DIFFERENCE_VARIABLES
        ),
        "cams_expected_semantic_difference_reason": CAMS_ROLLING_WINDOW_WAIVER_REASON,
        "jobs_expected": len(jobs),
        "jobs_completed": len(results),
        "values_compared": sum(item["values"] for item in results),
        "interpolated_values_excluded": sum(
            item["excluded_interpolated_values"] for item in results
        ),
        "semantic_waiver_values_observed": sum(
            item["semantic_waiver_values"] for item in results
        ),
        "semantic_waiver_mismatches_observed": sum(
            item["semantic_waiver_mismatches"] for item in results
        ),
        "gfs_single_batch_boundary_values_excluded": sum(
            item["gfs_single_batch_boundary_values_excluded"] for item in results
        ),
        "gfs_single_batch_boundary_differences": {
            "reason": GFS_SINGLE_BATCH_F000_REASON,
            "allowed_direction": "shanghai_non_null_singapore_null_only",
            "variables": sorted(GFS_SINGLE_BATCH_F000_MISSING),
            "by_variable": gfs_single_batch_by_variable,
            "examples": gfs_single_batch_examples,
        },
        "expected_semantic_differences": {
            "reason": CAMS_ROLLING_WINDOW_WAIVER_REASON,
            "variables": sorted(CAMS_EXPECTED_SEMANTIC_DIFFERENCE_VARIABLES),
            "by_variable": semantic_waiver_by_variable,
            "examples": semantic_waiver_examples,
        },
        "mismatches": mismatches[:100],
        "field_mismatch_totals": field_mismatch_totals,
        "errors": errors[:100],
        "job_digests": results,
    }
    output = Path(args.output_report)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({key: report[key] for key in ("passed", "point_count", "gfs_hours", "cams_hours", "jobs_completed", "values_compared")}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
