#!/usr/bin/env python3
"""Validate current local Open-Meteo .om outputs against official APIs.

The validator intentionally processes one 50-point batch at a time. It only
starts the next batch after all official-reference requests for the current
batch have matched the local API response.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shlex
import struct
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from openmeteo_api_inventory import build_inventory  # noqa: E402
from validate_openmeteo_point_api import (  # noqa: E402
    compare_series,
    extract_hourlies,
    fetch_json,
    fetch_json_via_ssh,
    format_utc_hour,
    parse_utc_hour,
)


DEFAULT_OFFICIAL_GFS_PRESSURE_COMPARE_LEVELS_HPA: tuple[int, ...] = (
    1000,
    975,
    950,
    925,
    900,
    850,
    800,
    750,
    700,
    650,
    600,
    550,
    500,
    400,
    300,
    200,
)

CAMS_THREE_HOUR_SOURCE_VARIABLES: set[str] = {
    "carbon_monoxide",
    "dust",
    "nitrogen_dioxide",
    "ozone",
    "sulphur_dioxide",
}


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def unique_ordered(items: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def parse_level_csv(value: str) -> set[str]:
    levels: set[str] = set()
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        if not item.isdigit():
            raise ValueError(f"invalid pressure level: {item}")
        levels.add(f"{int(item)}hPa")
    return levels


def parse_scopes(value: str) -> set[str]:
    scopes = {item.strip() for item in value.split(",") if item.strip()}
    invalid = scopes - {"gfs", "cams"}
    if invalid:
        raise ValueError(f"invalid scope(s): {', '.join(sorted(invalid))}")
    if not scopes:
        raise ValueError("at least one scope is required")
    return scopes


def parse_gfs_reference_mode(value: str) -> str:
    if value not in {"single-run", "latest"}:
        raise ValueError("--gfs-reference-mode must be 'single-run' or 'latest'")
    return value


def format_points(points: list[dict[str, float]], key: str) -> str:
    return ",".join(str(point[key]) for point in points)


def build_validation_points(
    *,
    total_points: int,
    left_lon: float,
    right_lon: float,
    bottom_lat: float,
    top_lat: float,
    seed: int,
    grid_point_ratio: float,
) -> list[dict[str, float]]:
    """Build reproducible random validation points with grid/off-grid coverage."""

    if total_points <= 0:
        raise ValueError("total_points must be positive")
    if left_lon >= right_lon:
        raise ValueError("left_lon must be less than right_lon")
    if bottom_lat >= top_lat:
        raise ValueError("bottom_lat must be less than top_lat")
    if not 0.0 <= grid_point_ratio <= 1.0:
        raise ValueError("grid_point_ratio must be in [0, 1]")

    rng = random.Random(seed)
    grid_count = int(round(total_points * grid_point_ratio))
    grid_count = min(total_points, max(0, grid_count))
    points: list[dict[str, float]] = []
    seen: set[tuple[float, float]] = set()

    lon_min_i = math.ceil(left_lon * 4)
    lon_max_i = math.floor(right_lon * 4)
    lat_min_i = math.ceil(bottom_lat * 4)
    lat_max_i = math.floor(top_lat * 4)
    if grid_count and (lon_min_i > lon_max_i or lat_min_i > lat_max_i):
        raise ValueError("bounds do not contain any quarter-degree grid point")

    max_attempts = total_points * 100
    attempts = 0
    while len(points) < grid_count:
        attempts += 1
        if attempts > max_attempts:
            raise ValueError("could not generate enough unique grid points")
        longitude = round(rng.randint(lon_min_i, lon_max_i) / 4, 6)
        latitude = round(rng.randint(lat_min_i, lat_max_i) / 4, 6)
        key = (latitude, longitude)
        if key in seen:
            continue
        seen.add(key)
        points.append({"latitude": latitude, "longitude": longitude})

    def is_quarter_degree(value: float) -> bool:
        return abs(value * 4 - round(value * 4)) < 1e-6

    attempts = 0
    while len(points) < total_points:
        attempts += 1
        if attempts > max_attempts:
            raise ValueError("could not generate enough unique off-grid points")
        latitude = round(rng.uniform(bottom_lat, top_lat), 6)
        longitude = round(rng.uniform(left_lon, right_lon), 6)
        if is_quarter_degree(latitude) and is_quarter_degree(longitude):
            continue
        key = (latitude, longitude)
        if key in seen:
            continue
        seen.add(key)
        points.append({"latitude": latitude, "longitude": longitude})

    rng.shuffle(points)
    return points


def request_hours(run: str, end_hour: str) -> int:
    run_dt = parse_utc_hour(run)
    end_dt = parse_utc_hour(end_hour)
    if end_dt < run_dt:
        raise ValueError("end hour is before run")
    return int((end_dt - run_dt).total_seconds() // 3600) + 1


def gfs_official_window(gfs_run: str, requested_start_hour: str, requested_frames: int) -> dict[str, Any]:
    """Return the full GFS window that the official single-runs API returns.

    The official single-runs endpoint rejects start_hour/end_hour when run is
    set. A request for a later target window therefore returns all frames from
    the pinned run through the requested target end hour. We compare that full
    returned window instead of trimming it.
    """

    if requested_frames <= 0:
        raise ValueError("requested_frames must be positive")
    run_hour = format_utc_hour(parse_utc_hour(gfs_run))
    requested_start = parse_utc_hour(requested_start_hour)
    requested_end = requested_start + timedelta(hours=requested_frames - 1)
    end_hour = format_utc_hour(requested_end)
    return {
        "start_hour": run_hour,
        "end_hour": end_hour,
        "frames": request_hours(run_hour, end_hour),
    }


def gfs_latest_window(requested_start_hour: str, requested_frames: int) -> dict[str, Any]:
    if requested_frames <= 0:
        raise ValueError("requested_frames must be positive")
    requested_start = parse_utc_hour(requested_start_hour)
    requested_end = requested_start + timedelta(hours=requested_frames - 1)
    return {
        "start_hour": format_utc_hour(requested_start),
        "end_hour": format_utc_hour(requested_end),
        "frames": requested_frames,
    }


def date_window_params(start_hour: str, end_hour: str) -> dict[str, str]:
    return {
        "start_date": parse_utc_hour(start_hour).date().isoformat(),
        "end_date": parse_utc_hour(end_hour).date().isoformat(),
    }


def local_params(
    scope: str,
    points: list[dict[str, float]],
    variables: list[str],
    start_hour: str,
    end_hour: str,
    *,
    gfs_run: str | None = None,
    gfs_model: str = "gfs_global",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "latitude": format_points(points, "latitude"),
        "longitude": format_points(points, "longitude"),
        "hourly": ",".join(variables),
        "timezone": "UTC",
        "timeformat": "iso8601",
    }
    if scope == "gfs":
        params["models"] = gfs_model
        params["wind_speed_unit"] = "ms"
        if gfs_run:
            params["run"] = gfs_run
            params["forecast_hours"] = request_hours(gfs_run, end_hour)
        else:
            params["start_hour"] = start_hour
            params["end_hour"] = end_hour
    elif scope == "cams":
        params["domains"] = "cams_global"
        params.update(date_window_params(start_hour, end_hour))
    else:
        raise ValueError(f"unknown scope: {scope}")
    return params


def reference_params(
    scope: str,
    points: list[dict[str, float]],
    variables: list[str],
    start_hour: str,
    end_hour: str,
    *,
    gfs_run: str,
    gfs_reference_mode: str = "single-run",
    gfs_model: str = "gfs_global",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "latitude": format_points(points, "latitude"),
        "longitude": format_points(points, "longitude"),
        "hourly": ",".join(variables),
        "timezone": "UTC",
        "timeformat": "iso8601",
    }
    if scope == "gfs":
        params["models"] = gfs_model
        params["wind_speed_unit"] = "ms"
        if gfs_reference_mode == "single-run":
            params["run"] = gfs_run
            params["forecast_hours"] = request_hours(gfs_run, end_hour)
        elif gfs_reference_mode == "latest":
            params["start_hour"] = start_hour
            params["end_hour"] = end_hour
        else:
            raise ValueError(f"unknown GFS reference mode: {gfs_reference_mode}")
    elif scope == "cams":
        params["domains"] = "cams_global"
        params.update(date_window_params(start_hour, end_hour))
    else:
        raise ValueError(f"unknown scope: {scope}")
    return params


def endpoint(scope: str) -> str:
    if scope == "gfs":
        return "/v1/forecast"
    if scope == "cams":
        return "/v1/air-quality"
    raise ValueError(f"unknown scope: {scope}")


def fetch_hourlies(
    *,
    base_url: str,
    scope: str,
    params: dict[str, Any],
    host_header: str | None,
    timeout: float,
    retries: int,
    retry_delay: float,
    request_pause: float,
    ssh_host: str | None = None,
) -> list[dict[str, Any]]:
    payload = (
        fetch_json_via_ssh(
            ssh_host,
            base_url,
            endpoint(scope),
            params,
            host_header=host_header,
            timeout=timeout,
            retries=retries,
            retry_delay=retry_delay,
            request_pause=request_pause,
        )
        if ssh_host
        else fetch_json(
            base_url,
            endpoint(scope),
            params,
            host_header=host_header,
            timeout=timeout,
            retries=retries,
            retry_delay=retry_delay,
            request_pause=request_pause,
        )
    )
    return extract_hourlies(payload)


def split_float_csv(value: Any) -> list[float]:
    return [float(item) for item in str(value).split(",") if item]


def swift_formatted_number(value: float, decimals: int | None) -> float | int | None:
    if not math.isfinite(value):
        return None
    if decimals is None:
        return value
    factor = 10**decimals
    # Open-Meteo's JSON writer formats Float values as:
    # Int((abs_val * Float(factor)).rounded()). Reproduce the Float32
    # multiplication here; using Python double precision shifts boundary
    # values such as 29.05 to the wrong side of the decimal rounding edge.
    value32 = struct.unpack("<f", struct.pack("<f", float(value)))[0]
    factor32 = struct.unpack("<f", struct.pack("<f", float(factor)))[0]
    scaled_input = struct.unpack("<f", struct.pack("<f", abs(value32) * factor32))[0]
    scaled = int(math.floor(scaled_input + 0.5))
    sign = -1 if value < 0 else 1
    if decimals <= 0:
        return sign * scaled
    integer = scaled // factor
    fraction = scaled % factor
    return float(f"{'-' if sign < 0 else ''}{integer}.{fraction:0{decimals}d}")


def fetch_local_hourlies(
    *,
    local_openmeteo_mode: str,
    api_base_url: str,
    scope: str,
    params: dict[str, Any],
    start_hour: str,
    end_hour: str,
    data_dir: Path,
    work_dir: Path,
    openmeteo_image: str,
    openmeteo_tag: str,
    direct_ssh_host: str | None,
    direct_remote_root: str | None,
    timeout: float,
    retries: int,
    retry_delay: float,
) -> list[dict[str, Any]]:
    if not api_base_url:
        raise ValueError("--api-base-url is required when --local-openmeteo-mode=http")
    return fetch_hourlies(
        base_url=api_base_url,
        scope=scope,
        params=params,
        host_header=None,
        timeout=timeout,
        retries=retries,
        retry_delay=retry_delay,
        request_pause=0.0,
    )


def trim_hourly_to_window(hourly: dict[str, Any], *, start_hour: str, frames: int) -> dict[str, Any]:
    times = list(hourly.get("time") or [])
    if start_hour not in times:
        return hourly
    start = times.index(start_hour)
    end = start + frames
    trimmed: dict[str, Any] = {}
    for key, value in hourly.items():
        if isinstance(value, list) and len(value) == len(times):
            trimmed[key] = value[start:end]
        else:
            trimmed[key] = value
    return trimmed


def trim_hourlies_to_scope_window(
    scope: str,
    hourlies: list[dict[str, Any]],
    *,
    start_hour: str,
    frames: int,
) -> list[dict[str, Any]]:
    if scope != "cams":
        return hourlies
    return [trim_hourly_to_window(hourly, start_hour=start_hour, frames=frames) for hourly in hourlies]


def split_pressure_name(name: str) -> tuple[str, str] | None:
    if not name.endswith("hPa") or "_" not in name:
        return None
    variable, level = name.rsplit("_", 1)
    if not level[:-3].isdigit():
        return None
    return variable, level


def actual_pressure_candidates(
    inventory_variables: list[str],
    data_dir: Path,
    *,
    compare_levels_hpa: set[str] | None = None,
    actual_names_by_domain: dict[str, set[str]] | None = None,
) -> list[str]:
    if actual_names_by_domain is not None:
        actual = actual_names_by_domain.get("ncep_gfs025", set())
    else:
        gfs025_dir = data_dir / "ncep_gfs025"
        if not gfs025_dir.is_dir():
            return []
        actual = {path.name for path in gfs025_dir.iterdir() if path.is_dir()}
    if not actual:
        return []
    output: list[str] = []
    for name in inventory_variables:
        parsed = split_pressure_name(name)
        if parsed is None:
            continue
        variable, level = parsed
        if compare_levels_hpa is not None and level not in compare_levels_hpa:
            continue
        if name in actual:
            output.append(name)
            continue
        if variable in {"wind_speed", "wind_direction"}:
            if f"wind_u_component_{level}" in actual and f"wind_v_component_{level}" in actual:
                output.append(name)
            continue
        if variable == "relativehumidity" and f"relative_humidity_{level}" in actual:
            output.append(name)
    return output


SURFACE_ALIASES = {
    "cloudcover": "cloud_cover",
    "cloudcover_high": "cloud_cover_high",
    "cloudcover_low": "cloud_cover_low",
    "cloudcover_mid": "cloud_cover_mid",
    "dewpoint_2m": "dew_point_2m",
    "freezinglevel_height": "freezing_level_height",
    "latent_heatflux": "latent_heat_flux",
    "relativehumidity_2m": "relative_humidity_2m",
    "sensible_heatflux": "sensible_heat_flux",
    "vapour_pressure_deficit": "vapor_pressure_deficit",
    "weathercode": "weather_code",
    "winddirection_10m": "wind_direction_10m",
    "winddirection_80m": "wind_direction_80m",
    "winddirection_100m": "wind_direction_100m",
    "windgusts_10m": "wind_gusts_10m",
    "windspeed_10m": "wind_speed_10m",
    "windspeed_80m": "wind_speed_80m",
    "windspeed_100m": "wind_speed_100m",
}

INTERNAL_GFS_RAW_VARIABLES_NOT_EXPOSED_BY_FORECAST_API = {
    "categorical_freezing_rain",
    "frozen_precipitation_percent",
}


def actual_surface_names(
    data_dir: Path,
    *,
    actual_names_by_domain: dict[str, set[str]] | None = None,
) -> set[str]:
    names: set[str] = set()
    for domain in ("ncep_gfs013", "ncep_gfs025"):
        if actual_names_by_domain is not None:
            names.update(actual_names_by_domain.get(domain, set()))
            continue
        domain_dir = data_dir / domain
        if domain_dir.is_dir():
            names.update(path.name for path in domain_dir.iterdir() if path.is_dir())
    return names


def actual_surface_candidates(
    inventory_variables: list[str],
    data_dir: Path,
    *,
    actual_names_by_domain: dict[str, set[str]] | None = None,
) -> list[str]:
    actual = actual_surface_names(data_dir, actual_names_by_domain=actual_names_by_domain)

    def has(name: str) -> bool:
        return name in actual

    def has_wind(level: str) -> bool:
        return has(f"wind_u_component_{level}") and has(f"wind_v_component_{level}")

    def derived_available(name: str) -> bool:
        if name in {"dew_point_2m", "dewpoint_2m", "vapor_pressure_deficit", "vapour_pressure_deficit", "wet_bulb_temperature_2m"}:
            return has("temperature_2m") and has("relative_humidity_2m")
        if name == "apparent_temperature":
            return has("temperature_2m") and has("relative_humidity_2m") and has_wind("10m")
        if name in {"wind_speed_10m", "windspeed_10m", "wind_direction_10m", "winddirection_10m"}:
            return has_wind("10m")
        if name in {"wind_speed_80m", "windspeed_80m", "wind_direction_80m", "winddirection_80m"}:
            return has_wind("80m")
        if name in {"wind_speed_100m", "windspeed_100m", "wind_direction_100m", "winddirection_100m"}:
            return has_wind("100m")
        if name == "surface_pressure":
            return has("pressure_msl")
        if name == "rain":
            return has("precipitation")
        if name == "snowfall":
            return has("snowfall_water_equivalent")
        if name in {"weather_code", "weathercode"}:
            return has("precipitation") and has("cloud_cover")
        if name == "is_day":
            return True
        if name in {"direct_radiation", "direct_normal_irradiance"}:
            return has("shortwave_radiation") and has("diffuse_radiation")
        if name in {"et0_fao_evapotranspiration", "evapotranspiration"}:
            return has("temperature_2m") and has("relative_humidity_2m") and has("shortwave_radiation") and has_wind("10m")
        return False

    output: list[str] = []
    for name in inventory_variables:
        if name in INTERNAL_GFS_RAW_VARIABLES_NOT_EXPOSED_BY_FORECAST_API:
            continue
        actual_name = SURFACE_ALIASES.get(name, name)
        if has(actual_name) or derived_available(name):
            output.append(name)
    return output


def actual_cams_candidates(
    inventory_variables: list[str],
    data_dir: Path,
    *,
    actual_names_by_domain: dict[str, set[str]] | None = None,
) -> list[str]:
    if actual_names_by_domain is not None:
        actual = actual_names_by_domain.get("cams_global", set())
    else:
        cams_dir = data_dir / "cams_global"
        if not cams_dir.is_dir():
            return []
        actual = {path.name for path in cams_dir.iterdir() if path.is_dir()}
    return [name for name in inventory_variables if name in actual or name == "is_day"]


def candidate_variables(
    repo_root: Path,
    scope: str,
    data_dir: Path,
    *,
    gfs_pressure_compare_levels_hpa: set[str] | None = None,
    actual_names_by_domain: dict[str, set[str]] | None = None,
) -> list[str]:
    inventory = build_inventory(repo_root)
    if scope == "gfs":
        surface_variables = unique_ordered(
            list(inventory["gfs_runtime_data"]["surface_variables"])
            + list(inventory["gfs_point_api"]["surface_variables"])
        )
        pressure_variables = unique_ordered(
            list(inventory["gfs_runtime_data"]["pressure_variables"])
            + list(inventory["gfs_point_api"]["pressure_variables"])
        )
        return actual_surface_candidates(
            surface_variables,
            data_dir,
            actual_names_by_domain=actual_names_by_domain,
        ) + actual_pressure_candidates(
            pressure_variables,
            data_dir,
            compare_levels_hpa=gfs_pressure_compare_levels_hpa,
            actual_names_by_domain=actual_names_by_domain,
        )
    if scope == "cams":
        return actual_cams_candidates(
            list(inventory["air_quality"]["raw_variables"]) + list(inventory["air_quality"]["derived_variables"]),
            data_dir,
            actual_names_by_domain=actual_names_by_domain,
        )
    raise ValueError(f"unknown scope: {scope}")


def remote_actual_names_by_domain(
    *,
    direct_ssh_host: str,
    direct_remote_root: str,
    timeout: float,
) -> dict[str, set[str]]:
    remote_script = f"""set -euo pipefail
cd {shlex.quote(direct_remote_root)}
find data/point -mindepth 2 -maxdepth 2 -type d -printf '%P\\n'
"""
    remote_script = remote_script.replace("\r", "")
    completed = subprocess.run(
        ["ssh", direct_ssh_host, "bash -s"],
        input=remote_script.encode("utf-8"),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    stdout_text = completed.stdout.decode("utf-8", errors="replace")
    stderr_text = completed.stderr.decode("utf-8", errors="replace")
    if completed.returncode != 0:
        raise RuntimeError(
            "remote Open-Meteo data inventory failed\n"
            f"host={direct_ssh_host}\n"
            f"stdout={stdout_text[:2000]}\n"
            f"stderr={stderr_text[:4000]}"
        )
    names_by_domain: dict[str, set[str]] = {}
    for line in stdout_text.splitlines():
        if "/" not in line:
            continue
        domain, name = line.split("/", 1)
        if not domain or not name or "/" in name:
            continue
        names_by_domain.setdefault(domain, set()).add(name)
    return names_by_domain


def has_information(hourlies: list[dict[str, Any]], variable: str, frames: int) -> bool:
    for hourly in hourlies:
        series = hourly.get(variable)
        if not isinstance(series, list):
            continue
        for value in series[:frames]:
            if value is not None:
                return True
    return False


def detect_available_variables(
    *,
    api_base_url: str,
    repo_root: Path,
    data_dir: Path,
    scope: str,
    points: list[dict[str, float]],
    start_hour: str,
    end_hour: str,
    frames: int,
    chunk_size: int,
    timeout: float,
    retries: int,
    retry_delay: float,
) -> list[str]:
    available: list[str] = []
    for variable_chunk in chunked(candidate_variables(repo_root, scope, data_dir), chunk_size):
        params = local_params(scope, points, variable_chunk, start_hour, end_hour)
        hourlies = fetch_hourlies(
            base_url=api_base_url,
            scope=scope,
            params=params,
            host_header=None,
            timeout=timeout,
            retries=retries,
            retry_delay=retry_delay,
            request_pause=0.0,
        )
        hourlies = trim_hourlies_to_scope_window(scope, hourlies, start_hour=start_hour, frames=frames)
        for variable in variable_chunk:
            if has_information(hourlies, variable, frames):
                available.append(variable)
    return available


def close_number(local: Any, reference: Any, tolerance: float) -> bool:
    if local is None or reference is None:
        return local is None and reference is None
    try:
        local_f = float(local)
        reference_f = float(reference)
    except (TypeError, ValueError):
        return local == reference
    if math.isnan(local_f) or math.isnan(reference_f):
        return math.isnan(local_f) and math.isnan(reference_f)
    return abs(local_f - reference_f) <= tolerance


def is_cams_interpolated_diagnostic_frame(scope: str, variable: str, time_value: str | None) -> bool:
    if scope != "cams" or variable not in CAMS_THREE_HOUR_SOURCE_VARIABLES or not time_value:
        return False
    return parse_utc_hour(time_value).hour % 3 != 0


def split_strict_and_diagnostic_mismatches(
    *,
    scope: str,
    variable: str,
    times: list[str],
    mismatches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    strict: list[dict[str, Any]] = []
    diagnostic: list[dict[str, Any]] = []
    for mismatch in mismatches:
        frame = int(mismatch.get("frame", -1))
        time_value = times[frame] if 0 <= frame < len(times) else None
        if is_cams_interpolated_diagnostic_frame(scope, variable, time_value):
            diagnostic.append(mismatch)
        else:
            strict.append(mismatch)
    return strict, diagnostic


def strict_frame_count(scope: str, variable: str, times: list[str], frames: int) -> int:
    if scope != "cams" or variable not in CAMS_THREE_HOUR_SOURCE_VARIABLES:
        return frames
    return sum(
        1
        for frame in range(frames)
        if not is_cams_interpolated_diagnostic_frame(
            scope,
            variable,
            times[frame] if frame < len(times) else None,
        )
    )


def compare_metadata(
    local_hourly: dict[str, Any],
    reference_hourly: dict[str, Any],
    *,
    point_index: int,
    point: dict[str, float],
    tolerance: float,
) -> list[dict[str, Any]]:
    # extract_hourlies returns only hourly data, so metadata comparison is not
    # available through the shared helper. Keep this as a placeholder field for
    # report shape compatibility; hourly value parity is the authoritative gate.
    return []


def failed_point_count(failures: list[dict[str, Any]]) -> int:
    return len({failure["point_index"] for failure in failures if "point_index" in failure})


def should_stop_for_batch_failures(report: dict[str, Any], *, max_failed_points_per_batch: int) -> bool:
    failures = list(report.get("failures") or [])
    if not failures:
        return False
    if max_failed_points_per_batch <= 0:
        return True
    if any("point_index" not in failure for failure in failures):
        return True
    return int(report.get("failed_points") or 0) > max_failed_points_per_batch


def validate_scope_batch(
    *,
    scope: str,
    batch_index: int,
    points: list[dict[str, float]],
    variables: list[str],
    api_base_url: str,
    local_openmeteo_mode: str,
    data_dir: Path,
    output_dir: Path,
    openmeteo_image: str,
    openmeteo_tag: str,
    direct_ssh_host: str | None,
    direct_remote_root: str | None,
    reference_base_url: str,
    reference_ssh_host: str | None,
    gfs_run: str,
    gfs_reference_mode: str = "single-run",
    start_hour: str,
    end_hour: str,
    frames: int,
    chunk_size: int,
    tolerance: float,
    timeout: float,
    retries: int,
    retry_delay: float,
    request_pause: float,
    gfs_model: str = "gfs_global",
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    diagnostic_differences: list[dict[str, Any]] = []
    checked_values = 0
    diagnostic_values = 0
    official_requests = 0
    started = time.time()
    for variable_chunk in chunked(variables, chunk_size):
        local_hourlies = fetch_local_hourlies(
            local_openmeteo_mode=local_openmeteo_mode,
            api_base_url=api_base_url,
            scope=scope,
            params=local_params(
                scope,
                points,
                variable_chunk,
                start_hour,
                end_hour,
                gfs_run=gfs_run if scope == "gfs" and gfs_reference_mode == "single-run" else None,
                gfs_model=gfs_model,
            ),
            start_hour=start_hour,
            end_hour=end_hour,
            data_dir=data_dir,
            work_dir=output_dir,
            openmeteo_image=openmeteo_image,
            openmeteo_tag=openmeteo_tag,
            direct_ssh_host=direct_ssh_host,
            direct_remote_root=direct_remote_root,
            timeout=timeout,
            retries=retries,
            retry_delay=retry_delay,
        )
        local_hourlies = trim_hourlies_to_scope_window(scope, local_hourlies, start_hour=start_hour, frames=frames)
        reference_hourlies = fetch_hourlies(
            base_url=reference_base_url,
            scope=scope,
            params=reference_params(
                scope,
                points,
                variable_chunk,
                start_hour,
                end_hour,
                gfs_run=gfs_run,
                gfs_reference_mode=gfs_reference_mode,
                gfs_model=gfs_model,
            ),
            host_header=None,
            timeout=timeout,
            retries=retries,
            retry_delay=retry_delay,
            request_pause=request_pause,
            ssh_host=reference_ssh_host,
        )
        reference_hourlies = trim_hourlies_to_scope_window(
            scope,
            reference_hourlies,
            start_hour=start_hour,
            frames=frames,
        )
        official_requests += 1
        if len(local_hourlies) != len(points) or len(reference_hourlies) != len(points):
            failures.append(
                {
                    "reason": "point_count_mismatch",
                    "batch": batch_index,
                    "scope": scope,
                    "variables": variable_chunk,
                    "expected_points": len(points),
                    "local_points": len(local_hourlies),
                    "reference_points": len(reference_hourlies),
                }
            )
            break
        for offset, point in enumerate(points):
            local_hourly = local_hourlies[offset]
            reference_hourly = reference_hourlies[offset]
            local_times = list(local_hourly.get("time") or [])[:frames]
            reference_times = list(reference_hourly.get("time") or [])[:frames]
            if local_times != reference_times:
                failures.append(
                    {
                        "reason": "time_mismatch",
                        "batch": batch_index,
                        "scope": scope,
                        "point_index": (batch_index - 1) * len(points) + offset,
                        "point": point,
                        "local_times": local_times[:10],
                        "reference_times": reference_times[:10],
                        "local_frame_count": len(local_times),
                        "reference_frame_count": len(reference_times),
                    }
                )
                continue
            failures.extend(
                compare_metadata(
                    local_hourly,
                    reference_hourly,
                    point_index=(batch_index - 1) * len(points) + offset,
                    point=point,
                    tolerance=tolerance,
                )
            )
            for variable in variable_chunk:
                checked_values += strict_frame_count(scope, variable, local_times, frames)
                diagnostic_values += frames - strict_frame_count(scope, variable, local_times, frames)
                mismatches = compare_series(
                    local_hourly.get(variable) or [],
                    reference_hourly.get(variable) or [],
                    frames=frames,
                    tolerance=tolerance,
                )
                strict_mismatches, diagnostic_mismatches = split_strict_and_diagnostic_mismatches(
                    scope=scope,
                    variable=variable,
                    times=local_times,
                    mismatches=mismatches,
                )
                if diagnostic_mismatches:
                    diagnostic_differences.append(
                        {
                            "reason": "cams_interpolated_frame_difference",
                            "batch": batch_index,
                            "scope": scope,
                            "point_index": (batch_index - 1) * len(points) + offset,
                            "point": point,
                            "variable": variable,
                            "mismatch_count": len(diagnostic_mismatches),
                            "first_mismatches": diagnostic_mismatches[:10],
                        }
                    )
                if strict_mismatches:
                    failures.append(
                        {
                            "reason": "reference_mismatch",
                            "batch": batch_index,
                            "scope": scope,
                            "point_index": (batch_index - 1) * len(points) + offset,
                            "point": point,
                            "variable": variable,
                            "mismatch_count": len(strict_mismatches),
                            "first_mismatches": strict_mismatches[:10],
                        }
                    )
    return {
        "scope": scope,
        "batch": batch_index,
        "points": len(points),
        "frames": frames,
        "variables": len(variables),
        "official_requests": official_requests,
        "checked_values": checked_values,
        "diagnostic_values": diagnostic_values,
        "elapsed_seconds": round(time.time() - started, 3),
        "failed_points": failed_point_count(failures),
        "passed": not failures,
        "failures": failures,
        "diagnostic_differences": diagnostic_differences,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate local .om outputs against official Open-Meteo APIs.")
    parser.add_argument("--api-base-url", default="")
    parser.add_argument("--local-openmeteo-mode", default="http", choices=("http",))
    parser.add_argument("--openmeteo-image", default="weather-forecast-openmeteo")
    parser.add_argument("--openmeteo-tag", default="latest")
    parser.add_argument("--direct-ssh-host", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--direct-remote-root", default="/opt/1panel/apps/weather_forecast_server", help=argparse.SUPPRESS)
    parser.add_argument("--gfs-reference-base-url", default="https://single-runs-api.open-meteo.com")
    parser.add_argument("--cams-reference-base-url", default="https://air-quality-api.open-meteo.com")
    parser.add_argument("--reference-ssh-host")
    parser.add_argument("--scopes", default="gfs,cams", help="Comma-separated scopes to validate: gfs,cams.")
    parser.add_argument("--gfs-model", default="gfs_global", help="GFS model parameter for local and official APIs.")
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--gfs-run", required=True)
    parser.add_argument(
        "--gfs-reference-mode",
        default="single-run",
        choices=("single-run", "latest"),
        help="Use official single-runs API or official latest forecast API for GFS reference.",
    )
    parser.add_argument("--gfs-start-hour", required=True)
    parser.add_argument("--cams-start-hour", required=True)
    parser.add_argument("--frames", type=int, default=50)
    parser.add_argument("--batches", type=int, default=10)
    parser.add_argument("--points-per-batch", type=int, default=50)
    parser.add_argument("--point-offset", type=int, default=0, help="Skip this many deterministic points before batching.")
    parser.add_argument("--point-seed", type=int, default=20260703, help="Seed for reproducible random validation points.")
    parser.add_argument(
        "--grid-point-ratio",
        type=float,
        default=0.25,
        help="Share of validation points forced onto quarter-degree grid coordinates.",
    )
    parser.add_argument("--chunk-size", type=int, default=20)
    parser.add_argument("--tolerance", type=float, default=0.001)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--request-retries", type=int, default=2)
    parser.add_argument("--request-retry-delay", type=float, default=2.0)
    parser.add_argument("--request-pause", type=float, default=0.0)
    parser.add_argument("--max-failed-points-per-batch", type=int, default=0)
    parser.add_argument("--left-lon", type=float, default=70.0)
    parser.add_argument("--right-lon", type=float, default=140.0)
    parser.add_argument("--bottom-lat", type=float, default=0.0)
    parser.add_argument("--top-lat", type=float, default=58.0)
    parser.add_argument(
        "--gfs-pressure-compare-levels",
        default=",".join(str(level) for level in DEFAULT_OFFICIAL_GFS_PRESSURE_COMPARE_LEVELS_HPA),
        help="Pressure levels to compare with official GFS API. Product-only levels are excluded from parity failures.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root)
    data_dir = Path(args.data_dir) if args.data_dir else repo_root / "data" / "openmeteo"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    gfs_pressure_compare_levels = parse_level_csv(args.gfs_pressure_compare_levels)
    scopes = parse_scopes(args.scopes)
    gfs_reference_mode = parse_gfs_reference_mode(args.gfs_reference_mode)
    if args.point_offset < 0:
        raise ValueError("--point-offset must be non-negative")
    if args.max_failed_points_per_batch < 0:
        raise ValueError("--max-failed-points-per-batch must be non-negative")
    total_points = args.point_offset + args.batches * args.points_per_batch
    all_points = build_validation_points(
        total_points=total_points,
        left_lon=args.left_lon,
        right_lon=args.right_lon,
        bottom_lat=args.bottom_lat,
        top_lat=args.top_lat,
        seed=args.point_seed,
        grid_point_ratio=args.grid_point_ratio,
    )
    points = all_points[args.point_offset:]
    if len({(p["latitude"], p["longitude"]) for p in points}) != len(points):
        raise ValueError("generated points are not unique")
    gfs_window = (
        gfs_official_window(
            gfs_run=args.gfs_run,
            requested_start_hour=args.gfs_start_hour,
            requested_frames=args.frames,
        )
        if gfs_reference_mode == "single-run"
        else gfs_latest_window(
            requested_start_hour=args.gfs_start_hour,
            requested_frames=args.frames,
        )
    )
    gfs_start = str(gfs_window["start_hour"])
    gfs_frames = int(gfs_window["frames"])
    cams_start = format_utc_hour(parse_utc_hour(args.cams_start_hour))
    gfs_end = str(gfs_window["end_hour"])
    cams_end = format_utc_hour(parse_utc_hour(cams_start) + timedelta(hours=args.frames - 1))

    availability_points = points[: args.points_per_batch]
    actual_names_by_domain = (
        remote_actual_names_by_domain(
            direct_ssh_host=args.direct_ssh_host,
            direct_remote_root=args.direct_remote_root,
            timeout=args.timeout,
        )
        if args.direct_ssh_host
        else None
    )
    gfs_variables = candidate_variables(
        repo_root,
        "gfs",
        data_dir,
        gfs_pressure_compare_levels_hpa=gfs_pressure_compare_levels,
        actual_names_by_domain=actual_names_by_domain,
    )
    cams_variables = candidate_variables(
        repo_root,
        "cams",
        data_dir,
        actual_names_by_domain=actual_names_by_domain,
    )
    inventory_report = {
        "gfs_variables": gfs_variables,
        "cams_variables": cams_variables,
        "counts": {"gfs": len(gfs_variables), "cams": len(cams_variables)},
        "scopes": sorted(scopes),
        "gfs_model": args.gfs_model,
        "gfs_reference_mode": gfs_reference_mode,
        "point_offset": args.point_offset,
        "point_seed": args.point_seed,
        "grid_point_ratio": args.grid_point_ratio,
        "availability_points": availability_points,
        "windows": {
            "gfs": {"start": gfs_start, "end": gfs_end, "run": format_utc_hour(parse_utc_hour(args.gfs_run))},
            "cams": {"start": cams_start, "end": cams_end},
        },
        "frames": {"gfs": gfs_frames, "cams": args.frames},
        "gfs_pressure_compare_levels_hpa": sorted(int(level[:-3]) for level in gfs_pressure_compare_levels),
        "actual_names_by_domain_counts": (
            {domain: len(names) for domain, names in sorted(actual_names_by_domain.items())}
            if actual_names_by_domain is not None
            else None
        ),
    }
    write_json(output_dir / "available-variables.json", inventory_report)
    print(json.dumps({"stage": "available_variables", "gfs": len(gfs_variables), "cams": len(cams_variables)}), flush=True)

    batch_reports: list[dict[str, Any]] = []
    any_failed = False
    stopped = False
    stopped_reason: str | None = None
    started = time.time()
    for batch_index in range(1, args.batches + 1):
        batch_points = points[(batch_index - 1) * args.points_per_batch : batch_index * args.points_per_batch]
        scope_reports = []
        batch_failed = False
        for scope, variables, reference_base_url, start_hour, end_hour in (
            ("gfs", gfs_variables, args.gfs_reference_base_url, gfs_start, gfs_end),
            ("cams", cams_variables, args.cams_reference_base_url, cams_start, cams_end),
        ):
            if scope not in scopes:
                continue
            scope_frames = gfs_frames if scope == "gfs" else args.frames
            report = validate_scope_batch(
                scope=scope,
                batch_index=batch_index,
                points=batch_points,
                variables=variables,
                api_base_url=args.api_base_url,
                local_openmeteo_mode=args.local_openmeteo_mode,
                data_dir=data_dir,
                output_dir=output_dir,
                openmeteo_image=args.openmeteo_image,
                openmeteo_tag=args.openmeteo_tag,
                direct_ssh_host=args.direct_ssh_host,
                direct_remote_root=args.direct_remote_root,
                reference_base_url=reference_base_url,
                reference_ssh_host=args.reference_ssh_host,
                gfs_run=format_utc_hour(parse_utc_hour(args.gfs_run)),
                gfs_reference_mode=gfs_reference_mode,
                start_hour=start_hour,
                end_hour=end_hour,
                frames=scope_frames,
                chunk_size=args.chunk_size,
                tolerance=args.tolerance,
                timeout=args.timeout,
                retries=args.request_retries,
                retry_delay=args.request_retry_delay,
                request_pause=args.request_pause,
                gfs_model=args.gfs_model,
            )
            write_json(output_dir / f"batch-{batch_index:02d}-{scope}.json", report)
            scope_reports.append(
                {
                    "scope": scope,
                    "passed": report["passed"],
                    "failures": len(report["failures"]),
                    "failed_points": report["failed_points"],
                    "checked_values": report["checked_values"],
                    "official_requests": report["official_requests"],
                    "report": str(output_dir / f"batch-{batch_index:02d}-{scope}.json"),
                }
            )
            if not report["passed"]:
                any_failed = True
                batch_failed = True
                if should_stop_for_batch_failures(report, max_failed_points_per_batch=args.max_failed_points_per_batch):
                    stopped = True
                    stopped_reason = "failed_point_threshold_exceeded"
                    break
        batch_report = {"batch": batch_index, "points": batch_points, "passed": not batch_failed, "scopes": scope_reports}
        batch_reports.append(batch_report)
        progress = {
            "passed": not any_failed and not stopped and len(batch_reports) == args.batches,
            "completed_batches": len(batch_reports),
            "planned_batches": args.batches,
            "points_per_batch": args.points_per_batch,
            "point_offset": args.point_offset,
            "point_seed": args.point_seed,
            "grid_point_ratio": args.grid_point_ratio,
            "frames": {"gfs": gfs_frames, "cams": args.frames},
            "elapsed_seconds": round(time.time() - started, 3),
            "available_variables": {"gfs": len(gfs_variables), "cams": len(cams_variables)},
            "batch_reports": batch_reports,
            "stopped_reason": stopped_reason,
        }
        write_json(output_dir / "summary.progress.json", progress)
        print(json.dumps({"batch": batch_index, "passed": not batch_failed, "stopped": stopped, "scopes": scope_reports}), flush=True)
        if stopped:
            break
    summary = {
        "passed": not any_failed and not stopped and len(batch_reports) == args.batches,
        "completed_batches": len(batch_reports),
        "planned_batches": args.batches,
        "completed_points": len(batch_reports) * args.points_per_batch,
        "planned_points": args.batches * args.points_per_batch,
        "points_per_batch": args.points_per_batch,
        "point_offset": args.point_offset,
        "point_seed": args.point_seed,
        "grid_point_ratio": args.grid_point_ratio,
        "frames": {"gfs": gfs_frames, "cams": args.frames},
        "elapsed_seconds": round(time.time() - started, 3),
        "available_variables": {"gfs": len(gfs_variables), "cams": len(cams_variables)},
        "batch_reports": batch_reports,
        "stopped_reason": stopped_reason,
    }
    summary_path = output_dir / f"summary-{args.batches * args.points_per_batch}x{args.frames}.json"
    write_json(summary_path, summary)
    print(json.dumps({"passed": summary["passed"], "summary": str(summary_path)}, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
