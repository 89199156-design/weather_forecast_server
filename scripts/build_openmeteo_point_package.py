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
    "WEATHER_OPENMETEO_POINT_DIR",
    "./data/openmeteo_points/gfs013_point",
)
DEFAULT_API_BASE_URL = os.environ.get(
    "WEATHER_OPENMETEO_GFS_API_URL",
    "http://127.0.0.1:18080/v1/forecast",
)
DEFAULT_MODEL = os.environ.get("WEATHER_OPENMETEO_LAYER_MODEL", "gfs_global")
MISSING_VALUE = -32768


WEATHER_CODE_TEXTS = {
    0: "晴",
    1: "基本晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇雾",
    51: "小毛毛雨",
    53: "中等毛毛雨",
    55: "浓毛毛雨",
    56: "小冻毛毛雨",
    57: "冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "小冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "中等阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴强冰雹",
}

PRECIP_PHASE_TEXTS = {
    0: "无明显降水",
    1: "雨",
    2: "雪",
    4: "冻雨风险",
}


@dataclass(frozen=True)
class PointField:
    name: str
    unit: str
    scale: float
    source: str | tuple[str, ...]
    multiplier: float = 1.0
    derive: str | None = None


POINT_FIELDS: tuple[PointField, ...] = (
    PointField("temperature_c", "C", 0.05, "temperature_2m"),
    PointField("apparent_temperature_c", "C", 0.05, "apparent_temperature"),
    PointField("dew_point_c", "C", 0.05, "dew_point_2m"),
    PointField("humidity_pct", "%", 1.0, "relative_humidity_2m"),
    PointField("u10_ms", "m/s", 0.1, "wind_u_component_10m"),
    PointField("v10_ms", "m/s", 0.1, "wind_v_component_10m"),
    PointField("wind_speed_ms", "m/s", 0.01, "wind_speed_10m"),
    PointField("wind_direction_deg", "deg", 1.0, "wind_direction_10m"),
    PointField("gust_ms", "m/s", 0.1, "wind_gusts_10m"),
    PointField("visibility_m", "m", 20.0, "visibility"),
    PointField("surface_pressure_pa", "Pa", 5.0, "surface_pressure", multiplier=100.0),
    PointField("mean_sea_level_pressure_pa", "Pa", 10.0, "pressure_msl", multiplier=100.0),
    PointField("precip_1h_mm", "mm", 0.1, "precipitation"),
    PointField("precip_rain_1h_mm", "mm", 0.1, "rain"),
    PointField("precip_showers_1h_mm", "mm", 0.1, "showers"),
    PointField("snowfall_cm", "cm", 0.1, "snowfall"),
    PointField("snow_depth_m", "m", 0.01, "snow_depth"),
    PointField("cloud_total_pct", "%", 1.0, "cloud_cover"),
    PointField("cloud_low_pct", "%", 1.0, "cloud_cover_low"),
    PointField("cloud_mid_pct", "%", 1.0, "cloud_cover_mid"),
    PointField("cloud_high_pct", "%", 1.0, "cloud_cover_high"),
    PointField("precip_phase_code", "code", 1.0, "weather_code", derive="precip_phase_from_weather_code"),
    PointField("thunderstorm_code", "code", 1.0, "weather_code", derive="thunderstorm_code_from_weather_code"),
    PointField("cape_jkg", "J/kg", 10.0, "cape"),
    PointField("uv_index", "index", 0.01, "uv_index"),
    PointField("uv_index_clear_sky", "index", 0.01, "uv_index_clear_sky"),
    PointField("is_day", "code", 1.0, "is_day"),
    PointField("weather_code", "code", 1.0, "weather_code"),
)


def required_variables(fields: Sequence[PointField]) -> list[str]:
    seen: set[str] = set()
    variables: list[str] = []
    for field in fields:
        sources = (field.source,) if isinstance(field.source, str) else field.source
        for source in sources:
            if source not in seen:
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


def derive_values(field: PointField, variables: Mapping[str, np.ndarray]) -> np.ndarray:
    if isinstance(field.source, str):
        values = np.asarray(variables[field.source], dtype=np.float32) * np.float32(field.multiplier)
    else:
        raise ValueError(f"unsupported multi-source point field: {field.name}")
    if field.derive == "precip_phase_from_weather_code":
        return layer_builder.precip_phase_from_weather_code(values)
    if field.derive == "thunderstorm_code_from_weather_code":
        return layer_builder.thunderstorm_code_from_weather_code(values)
    if field.derive:
        raise ValueError(f"unknown point field derive: {field.derive}")
    return values


def field_metadata(fields: Sequence[PointField]) -> list[dict[str, Any]]:
    return [
        {
            "name": field.name,
            "dtype": "int16",
            "scale": field.scale,
            "offset": 0.0,
            "unit": field.unit,
            "missing_value": MISSING_VALUE,
            "source": field.source,
            **({"derive": field.derive} if field.derive else {}),
            **({"multiplier": field.multiplier} if field.multiplier != 1.0 else {}),
        }
        for field in fields
    ]


def valid_timestamps(times: Sequence[str]) -> list[int]:
    return [int(layer_builder.parse_utc_hour(value).timestamp()) for value in times]


def fill_point_package(
    *,
    package: np.memmap,
    response: list[dict[str, Any]],
    flat_indices: Sequence[int],
    fields: Sequence[PointField],
    variables: Sequence[str],
    expected_times: Sequence[str],
    grid: layer_builder.RegionGrid,
) -> None:
    if len(response) != len(flat_indices):
        raise ValueError(f"response point count mismatch: got {len(response)} expected {len(flat_indices)}")
    ys: list[int] = []
    xs: list[int] = []
    for flat_index in flat_indices:
        y, x, _lat, _lon = grid.point_for_flat_index(flat_index)
        ys.append(y)
        xs.append(x)

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
    for field_index, field in enumerate(fields):
        values = derive_values(field, variable_values)
        encoded = encode_values(values, scale=field.scale)
        package[y_index, x_index, :, field_index] = encoded


def publish_package(build_dir: Path, output_dir: Path, manifest: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("point_weather.bin", "point_weather_meta.json"):
        source = build_dir / name
        target = output_dir / name
        source.replace(target)


def request_forecast_hours_for_window(run: str | None, end_hour: str) -> int | None:
    return layer_builder.request_forecast_hours_for_window(run=run, end_hour=end_hour)


def build_point_package(
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
    grid = layer_builder.compute_gfs013_region_grid(
        left_lon=left_lon,
        right_lon=right_lon,
        bottom_lat=bottom_lat,
        top_lat=top_lat,
    )
    fields = POINT_FIELDS
    variables = required_variables(fields)
    api_options = layer_builder.layer_api_options_for_scope("gfs")
    request_forecast_hours = request_forecast_hours_for_window(run, end_hour)
    build_dir = output_dir / f".build_point_{os.getpid()}_{int(time.time())}"
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

        bin_path = build_dir / "point_weather.bin"
        package = np.memmap(
            bin_path,
            dtype=np.int16,
            mode="w+",
            shape=(grid.height, grid.width, len(times), len(fields)),
        )
        package[:] = MISSING_VALUE
        fill_point_package(
            package=package,
            response=first_response,
            flat_indices=first_indices,
            fields=fields,
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
            fill_point_package(
                package=package,
                response=response,
                flat_indices=chunk_indices,
                fields=fields,
                variables=variables,
                expected_times=times,
                grid=grid,
            )
            completed_points += len(response)
            print(f"[openmeteo-point-package] fetched {completed_points}/{grid.flat_count()} grid points", flush=True)

        package.flush()
        del package

        start_dt = layer_builder.parse_utc_hour(start_hour)
        manifest = {
            "version": 1,
            "model": "GFS013_POINT_WEATHER",
            "batch": "openmeteo_gfs_global",
            "update_timestamp": int(start_dt.timestamp()),
            "generated_at": int(time.time()),
            "layout": "grid_height,grid_width,time,field",
            "dtype": "int16",
            "missing_value": MISSING_VALUE,
            "bin_file": "point_weather.bin",
            "time_count": len(times),
            "field_count": len(fields),
            "valid_timestamps": valid_timestamps(times),
            "weather_codes": WEATHER_CODE_TEXTS,
            "precip_phase_codes": PRECIP_PHASE_TEXTS,
            "derived_algorithms": {
                "weather_result": {
                    "algorithm_version": "openmeteo_gfs_global_api",
                    "source": "Open-Meteo forecast weather_code",
                },
                "precip_phase": {
                    "algorithm_version": "openmeteo_weather_code_phase",
                    "source": "Open-Meteo forecast weather_code",
                },
                "thunderstorm_code": {
                    "algorithm_version": "openmeteo_weather_code_thunderstorm",
                    "source": "Open-Meteo forecast weather_code",
                },
            },
            "grid": grid.manifest(),
            "query_window": {
                "start_hour": start_hour,
                "end_hour": end_hour,
                "run": run,
                "request_forecast_hours": request_forecast_hours,
            },
            "fields": field_metadata(fields),
            "source": "openmeteo_api",
            "api_base_url": api_base_url,
            "api_host_header": api_host_header,
            "api_options": api_options,
            "variables": variables,
        }
        meta_path = build_dir / "point_weather_meta.json"
        meta_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        publish_package(build_dir, output_dir, manifest)
        return manifest
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build regional point weather package from the local Open-Meteo API.")
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
    parser.add_argument("--chunk-size", type=int, default=int(os.environ.get("WEATHER_OPENMETEO_POINT_CHUNK_SIZE", "250")))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.environ.get("WEATHER_OPENMETEO_POINT_TIMEOUT", "120")))
    parser.add_argument("--request-retries", type=int, default=int(os.environ.get("WEATHER_OPENMETEO_POINT_REQUEST_RETRIES", "2")))
    parser.add_argument("--request-retry-delay", type=float, default=float(os.environ.get("WEATHER_OPENMETEO_POINT_REQUEST_RETRY_DELAY", "2")))
    parser.add_argument("--request-pause", type=float, default=float(os.environ.get("WEATHER_OPENMETEO_POINT_REQUEST_PAUSE", "0")))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_point_package(
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
        f"[openmeteo-point-package] published {manifest['time_count']} frames "
        f"grid={manifest['grid']['grid_width']}x{manifest['grid']['grid_height']} "
        f"output={args.output_dir}",
        flush=True,
    )


if __name__ == "__main__":
    main()
