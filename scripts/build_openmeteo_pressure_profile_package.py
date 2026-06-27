#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_openmeteo_layers as layer_builder


DEFAULT_OUTPUT_DIR = os.environ.get(
    "WEATHER_OPENMETEO_PRESSURE_PROFILE_DIR",
    "./data/openmeteo_points/pressure_profile",
)
DEFAULT_API_BASE_URL = os.environ.get(
    "WEATHER_OPENMETEO_GFS_API_URL",
    "http://127.0.0.1:18080/v1/forecast",
)
DEFAULT_MODEL = os.environ.get("WEATHER_OPENMETEO_LAYER_MODEL", "gfs_global")
MISSING_VALUE = -32768

PRESSURE_LEVELS_HPA: tuple[int, ...] = (
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


@dataclass(frozen=True)
class ProfileField:
    name: str
    unit: str
    scale: float
    source_template: str | None
    derive: str | None = None

    def source_for_level(self, level: int) -> str | None:
        if self.source_template is None:
            return None
        return self.source_template.format(level=level)


PROFILE_FIELDS: tuple[ProfileField, ...] = (
    ProfileField("geopotential_height_m", "m", 1.0, "geopotential_height_{level}hPa"),
    ProfileField("height_agl_m", "m", 1.0, "geopotential_height_{level}hPa", derive="height_agl_from_elevation"),
    ProfileField("temperature_c", "C", 0.05, "temperature_{level}hPa"),
    ProfileField("relative_humidity_pct", "%", 1.0, "relative_humidity_{level}hPa"),
    ProfileField("dew_point_c", "C", 0.05, "dew_point_{level}hPa"),
    ProfileField("cloud_cover_pct", "%", 1.0, "cloud_cover_{level}hPa"),
    ProfileField("wind_speed_ms", "m/s", 0.01, "wind_speed_{level}hPa"),
    ProfileField("wind_direction_deg", "deg", 1.0, "wind_direction_{level}hPa"),
)


def compute_gfs025_region_grid(
    *,
    left_lon: float,
    right_lon: float,
    bottom_lat: float,
    top_lat: float,
) -> layer_builder.RegionGrid:
    full_nx = 1440
    full_ny = 721
    lon_min = -180.0
    lat_min = -90.0
    dx = 0.25
    dy = 0.25
    epsilon = 1e-9
    x0 = max(0, int(np.ceil((left_lon - lon_min) / dx - epsilon)))
    x1 = min(full_nx - 1, int(np.floor((right_lon - lon_min) / dx + epsilon)))
    y0 = max(0, int(np.ceil((bottom_lat - lat_min) / dy - epsilon)))
    y1 = min(full_ny - 1, int(np.floor((top_lat - lat_min) / dy + epsilon)))
    if x0 > x1 or y0 > y1:
        raise ValueError("configured region does not overlap GFS025 source grid")

    width = x1 - x0 + 1
    height = y1 - y0 + 1
    region_lon_min = lon_min + float(x0) * dx
    region_lat_min = lat_min + float(y0) * dy
    region_lat_max = lat_min + float(y1) * dy
    longitude_values = [round(region_lon_min + float(x) * dx, 6) for x in range(width)]
    latitude_values = [round(region_lat_max - float(y) * dy, 6) for y in range(height)]
    return layer_builder.RegionGrid(
        width=width,
        height=height,
        lat_min=round(region_lat_min, 6),
        lon_min=round(region_lon_min, 6),
        dx=dx,
        dy=dy,
        latitude_values=latitude_values,
        longitude_values=longitude_values,
        row_order="north_to_south",
    )


def required_variables(fields: Sequence[ProfileField], levels: Sequence[int]) -> list[str]:
    seen: set[str] = set()
    variables: list[str] = []
    for level in levels:
        for field in fields:
            source = field.source_for_level(level)
            if source and source not in seen:
                seen.add(source)
                variables.append(source)
    return variables


def encode_values(values: np.ndarray, *, scale: float) -> np.ndarray:
    parsed = np.asarray(values, dtype=np.float64)
    valid = np.isfinite(parsed)
    encoded = np.full(parsed.shape, MISSING_VALUE, dtype=np.int16)
    if np.any(valid):
        raw = layer_builder.round_half_away_from_zero(parsed[valid] / float(scale))
        raw = np.clip(raw, -32767, 32767).astype(np.int16)
        encoded[valid] = raw
    return encoded


def derive_level_values(
    field: ProfileField,
    variables: Mapping[str, np.ndarray],
    *,
    level: int,
    elevations: np.ndarray,
) -> np.ndarray:
    source = field.source_for_level(level)
    if source is None:
        raise ValueError(f"profile field has no source: {field.name}")
    values = np.asarray(variables[source], dtype=np.float32)
    if field.derive == "height_agl_from_elevation":
        elevation_values = np.asarray(elevations, dtype=np.float32)
        if values.ndim == 2 and values.shape[0] == elevation_values.shape[0]:
            return values - elevation_values[:, None]
        return values - elevation_values
    if field.derive:
        raise ValueError(f"unknown profile field derive: {field.derive}")
    return values


def field_metadata(fields: Sequence[ProfileField]) -> list[dict[str, Any]]:
    return [
        {
            "name": field.name,
            "dtype": "int16",
            "scale": field.scale,
            "offset": 0.0,
            "unit": field.unit,
            "missing_value": MISSING_VALUE,
            "source_template": field.source_template,
            **({"derive": field.derive} if field.derive else {}),
        }
        for field in fields
    ]


def valid_timestamps(times: Sequence[str]) -> list[int]:
    return [int(layer_builder.parse_utc_hour(value).timestamp()) for value in times]


def fill_pressure_profile_package(
    *,
    package: np.memmap,
    response: list[dict[str, Any]],
    flat_indices: Sequence[int],
    fields: Sequence[ProfileField],
    levels: Sequence[int],
    variables: Sequence[str],
    expected_times: Sequence[str],
    grid: layer_builder.RegionGrid,
) -> None:
    if len(response) != len(flat_indices):
        raise ValueError(f"response point count mismatch: got {len(response)} expected {len(flat_indices)}")
    ys: list[int] = []
    xs: list[int] = []
    elevations: list[float] = []
    for item, flat_index in zip(response, flat_indices):
        y, x, _lat, _lon = grid.point_for_flat_index(flat_index)
        ys.append(y)
        xs.append(x)
        elevations.append(float(item.get("elevation", 0.0) or 0.0))

    variable_values: dict[str, np.ndarray] = {}
    for variable in variables:
        rows: list[Any] = []
        for item in response:
            hourly = item.get("hourly") or {}
            times = hourly.get("time") or []
            if list(times) != list(expected_times):
                raise ValueError("Open-Meteo response time axis changed between chunks")
            values = hourly.get(variable)
            if values is None:
                raise ValueError(f"Open-Meteo response missing variable {variable}")
            rows.append(values)
        variable_values[variable] = np.asarray(rows, dtype=np.float32)

    y_index = np.asarray(ys, dtype=np.intp)
    x_index = np.asarray(xs, dtype=np.intp)
    elevation_array = np.asarray(elevations, dtype=np.float32)
    for level_index, level in enumerate(levels):
        for field_index, field in enumerate(fields):
            values = derive_level_values(field, variable_values, level=level, elevations=elevation_array)
            encoded = encode_values(values, scale=field.scale)
            package[y_index, x_index, :, level_index, field_index] = encoded


def publish_package(build_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("pressure_profile.bin", "pressure_profile_meta.json"):
        source = build_dir / name
        target = output_dir / name
        source.replace(target)


def build_pressure_profile_package(
    *,
    api_base_url: str,
    output_dir: Path,
    start_hour: str,
    end_hour: str,
    left_lon: float,
    right_lon: float,
    bottom_lat: float,
    top_lat: float,
    chunk_size: int,
    timeout_seconds: float,
    model: str = DEFAULT_MODEL,
    api_host_header: str | None = None,
    run: str | None = None,
    request_retries: int = 0,
    request_retry_delay: float = 2.0,
    request_pause: float = 0.0,
) -> dict[str, Any]:
    grid = compute_gfs025_region_grid(
        left_lon=left_lon,
        right_lon=right_lon,
        bottom_lat=bottom_lat,
        top_lat=top_lat,
    )
    fields = PROFILE_FIELDS
    levels = PRESSURE_LEVELS_HPA
    variables = required_variables(fields, levels)
    api_options = layer_builder.layer_api_options_for_scope("gfs")
    request_forecast_hours = layer_builder.request_forecast_hours_for_window(run=run, end_hour=end_hour)
    build_dir = output_dir / f".build_pressure_profile_{os.getpid()}_{int(time.time())}"
    build_dir.mkdir(parents=True, exist_ok=True)

    try:
        chunk_iter = layer_builder.iter_flat_chunks(grid.flat_count(), chunk_size)
        first_chunk = next(chunk_iter)
        first_indices = list(first_chunk)
        first_latitudes: list[float] = []
        first_longitudes: list[float] = []
        for flat_index in first_indices:
            _y, _x, lat, lon = grid.point_for_flat_index(flat_index)
            first_latitudes.append(lat)
            first_longitudes.append(lon)
        first_response = layer_builder.fetch_layer_api_chunk(
            api_base_url=api_base_url,
            latitudes=first_latitudes,
            longitudes=first_longitudes,
            variables=variables,
            scope="gfs",
            model=model,
            domain=None,
            start_hour=start_hour,
            end_hour=end_hour,
            api_options=api_options,
            timeout_seconds=timeout_seconds,
            api_host_header=api_host_header,
            run=run,
            request_forecast_hours=request_forecast_hours,
            request_retries=request_retries,
            request_retry_delay=request_retry_delay,
            request_pause=request_pause,
        )
        if run:
            first_response = layer_builder.trim_response_to_time_window(
                first_response,
                start_hour=start_hour,
                end_hour=end_hour,
            )
        if not first_response:
            raise RuntimeError("Open-Meteo API returned no locations")
        times = list((first_response[0].get("hourly") or {}).get("time") or [])
        if not times:
            raise RuntimeError("Open-Meteo API returned no hourly time axis")

        bin_path = build_dir / "pressure_profile.bin"
        package = np.memmap(
            bin_path,
            dtype=np.int16,
            mode="w+",
            shape=(grid.height, grid.width, len(times), len(levels), len(fields)),
        )
        package[:] = MISSING_VALUE
        fill_pressure_profile_package(
            package=package,
            response=first_response,
            flat_indices=first_indices,
            fields=fields,
            levels=levels,
            variables=variables,
            expected_times=times,
            grid=grid,
        )

        completed_points = len(first_response)
        for chunk in chunk_iter:
            chunk_indices = list(chunk)
            latitudes: list[float] = []
            longitudes: list[float] = []
            for flat_index in chunk_indices:
                _y, _x, lat, lon = grid.point_for_flat_index(flat_index)
                latitudes.append(lat)
                longitudes.append(lon)
            response = layer_builder.fetch_layer_api_chunk(
                api_base_url=api_base_url,
                latitudes=latitudes,
                longitudes=longitudes,
                variables=variables,
                scope="gfs",
                model=model,
                domain=None,
                start_hour=start_hour,
                end_hour=end_hour,
                api_options=api_options,
                timeout_seconds=timeout_seconds,
                api_host_header=api_host_header,
                run=run,
                request_forecast_hours=request_forecast_hours,
                request_retries=request_retries,
                request_retry_delay=request_retry_delay,
                request_pause=request_pause,
            )
            if run:
                response = layer_builder.trim_response_to_time_window(
                    response,
                    start_hour=start_hour,
                    end_hour=end_hour,
                )
            fill_pressure_profile_package(
                package=package,
                response=response,
                flat_indices=chunk_indices,
                fields=fields,
                levels=levels,
                variables=variables,
                expected_times=times,
                grid=grid,
            )
            completed_points += len(response)
            print(
                f"[openmeteo-pressure-profile] fetched {completed_points}/{grid.flat_count()} grid points",
                flush=True,
            )

        package.flush()
        del package

        start_dt = layer_builder.parse_utc_hour(start_hour)
        manifest = {
            "version": 1,
            "model": "GFS025_PRESSURE_PROFILE",
            "batch": "openmeteo_gfs_global_pressure",
            "update_timestamp": int(start_dt.timestamp()),
            "generated_at": int(time.time()),
            "layout": "grid_height,grid_width,time,level,field",
            "dtype": "int16",
            "missing_value": MISSING_VALUE,
            "bin_file": "pressure_profile.bin",
            "time_count": len(times),
            "level_count": len(levels),
            "field_count": len(fields),
            "valid_timestamps": valid_timestamps(times),
            "pressure_levels_hpa": list(levels),
            "grid": grid.manifest() | {"grid_type": "openmeteo_gfs025_regular_latlon"},
            "query_window": {
                "start_hour": start_hour,
                "end_hour": end_hour,
                "run": run,
                "request_forecast_hours": request_forecast_hours,
                "frame_count": len(times),
            },
            "fields": field_metadata(fields),
            "source": "openmeteo_api",
            "api_base_url": api_base_url,
            "api_host_header": api_host_header,
            "api_options": api_options,
            "variables": variables,
            "derived_fields": {
                "height_agl_m": {
                    "source": "geopotential_height_m - Open-Meteo response elevation",
                    "weather_logic": "none",
                },
            },
        }
        meta_path = build_dir / "pressure_profile_meta.json"
        meta_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        publish_package(build_dir, output_dir)
        return manifest
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build regional pressure-profile package from the local Open-Meteo API.")
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--api-host-header")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--run", help="Pinned run for single-runs API mode, e.g. 2026-06-26T06:00.")
    parser.add_argument("--start-hour", required=True)
    parser.add_argument("--end-hour", required=True)
    parser.add_argument("--left-lon", type=float, default=float(os.environ.get("WEATHER_REGION_LEFT_LON", "70.0")))
    parser.add_argument("--right-lon", type=float, default=float(os.environ.get("WEATHER_REGION_RIGHT_LON", "140.0")))
    parser.add_argument("--bottom-lat", type=float, default=float(os.environ.get("WEATHER_REGION_BOTTOM_LAT", "0.0")))
    parser.add_argument("--top-lat", type=float, default=float(os.environ.get("WEATHER_REGION_TOP_LAT", "58.0")))
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=int(os.environ.get("WEATHER_OPENMETEO_PRESSURE_PROFILE_CHUNK_SIZE", "50")),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.environ.get("WEATHER_OPENMETEO_PRESSURE_PROFILE_TIMEOUT", "180")),
    )
    parser.add_argument(
        "--request-retries",
        type=int,
        default=int(os.environ.get("WEATHER_OPENMETEO_PRESSURE_PROFILE_REQUEST_RETRIES", "2")),
    )
    parser.add_argument(
        "--request-retry-delay",
        type=float,
        default=float(os.environ.get("WEATHER_OPENMETEO_PRESSURE_PROFILE_REQUEST_RETRY_DELAY", "2")),
    )
    parser.add_argument(
        "--request-pause",
        type=float,
        default=float(os.environ.get("WEATHER_OPENMETEO_PRESSURE_PROFILE_REQUEST_PAUSE", "0")),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_pressure_profile_package(
        api_base_url=args.api_base_url,
        output_dir=Path(args.output_dir),
        start_hour=args.start_hour,
        end_hour=args.end_hour,
        left_lon=args.left_lon,
        right_lon=args.right_lon,
        bottom_lat=args.bottom_lat,
        top_lat=args.top_lat,
        chunk_size=args.chunk_size,
        timeout_seconds=args.timeout_seconds,
        model=args.model,
        api_host_header=args.api_host_header,
        run=args.run,
        request_retries=args.request_retries,
        request_retry_delay=args.request_retry_delay,
        request_pause=args.request_pause,
    )
    print(
        f"[openmeteo-pressure-profile] published {manifest['time_count']} frames "
        f"levels={manifest['level_count']} fields={manifest['field_count']} "
        f"grid={manifest['grid']['grid_width']}x{manifest['grid']['grid_height']} "
        f"output={args.output_dir}",
        flush=True,
    )


if __name__ == "__main__":
    main()
