#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import requests
from PIL import Image


LAYER_PRODUCT_VERSION = 1
DEFAULT_API_BASE_URL = os.environ.get("WEATHER_OPENMETEO_API_URL", "http://127.0.0.1:18080/v1/forecast")
DEFAULT_OUTPUT_DIR = os.environ.get(
    "WEATHER_OPENMETEO_LAYER_DIR",
    "./data/openmeteo_layers/gfs013_surface",
)


@dataclass(frozen=True)
class RegionGrid:
    width: int
    height: int
    lat_min: float
    lon_min: float
    dx: float
    dy: float
    latitude_values: list[float]
    longitude_values: list[float]
    row_order: str

    def flat_count(self) -> int:
        return self.width * self.height

    def point_for_flat_index(self, flat_index: int) -> tuple[int, int, float, float]:
        y = flat_index // self.width
        x = flat_index - y * self.width
        return y, x, self.latitude_values[y], self.longitude_values[x]

    def manifest(self) -> dict[str, Any]:
        lon_min = self.longitude_values[0]
        lon_max = self.longitude_values[-1]
        lat_min = self.latitude_values[0]
        lat_max = self.latitude_values[-1]
        return {
            "grid_type": "openmeteo_gfs013_regular_latlon",
            "bounds_semantics": "point_centers",
            "sample_bounds": {
                "lon_min": lon_min,
                "lat_min": lat_min,
                "lon_max": lon_max,
                "lat_max": lat_max,
            },
            "display_bounds": {
                "lon_min": lon_min - self.dx / 2.0,
                "lat_min": lat_min - self.dy / 2.0,
                "lon_max": lon_max + self.dx / 2.0,
                "lat_max": lat_max + self.dy / 2.0,
            },
            "display_bounds_semantics": "outer_edges_approx",
            "grid_width": self.width,
            "grid_height": self.height,
            "center_dx": self.dx,
            "center_dy": self.dy,
            "longitude_values": self.longitude_values,
            "latitude_values": self.latitude_values,
            "row_order": self.row_order,
        }


@dataclass(frozen=True)
class LayerDefinition:
    name: str
    subdir: str
    api_variables: tuple[str, ...]
    render_var: str
    unit: str
    scale: float
    value_range: tuple[float, float]
    vmin: float = 0.0
    api_multiplier: float = 1.0
    data_type: str = "continuous"

    def manifest(self, grid: RegionGrid) -> dict[str, Any]:
        field: str | list[str] = self.api_variables[0] if len(self.api_variables) == 1 else list(self.api_variables)
        return {
            "field": field,
            "render_var": self.render_var,
            "unit": self.unit,
            "scale": self.scale,
            "range": list(self.value_range),
            "data_type": self.data_type,
            "interpolation": "linear",
            "source": "openmeteo_api",
            "api_variables": list(self.api_variables),
            "api_multiplier": self.api_multiplier,
            "grid": grid.manifest(),
            "subdir": self.subdir,
        }


DEFAULT_LAYER_DEFINITIONS: tuple[LayerDefinition, ...] = (
    LayerDefinition("cloud_total_1", "cloud_total_1", ("cloud_cover",), "tcc", "%", 100.0, (0.0, 100.0)),
    LayerDefinition("cloud_high_1", "cloud_high_1", ("cloud_cover_high",), "hcc", "%", 100.0, (0.0, 100.0)),
    LayerDefinition("cloud_mid_1", "cloud_mid_1", ("cloud_cover_mid",), "mcc", "%", 100.0, (0.0, 100.0)),
    LayerDefinition("cloud_low_1", "cloud_low_1", ("cloud_cover_low",), "lcc", "%", 100.0, (0.0, 100.0)),
    LayerDefinition("t2m", "t2m", ("temperature_2m",), "t2m", "C", 100.0, (-100.0, 100.0), vmin=-100.0),
    LayerDefinition("r2", "r2", ("relative_humidity_2m",), "r2", "%", 100.0, (0.0, 100.0)),
    LayerDefinition(
        "wind",
        "wind",
        ("wind_u_component_10m", "wind_v_component_10m"),
        "wind",
        "m/s",
        10.0,
        (-100.0, 100.0),
        data_type="vector",
    ),
    LayerDefinition("tp", "tp", ("precipitation",), "tp", "mm", 100.0, (0.0, 600.0)),
    LayerDefinition("snod", "snod", ("snow_depth",), "snod", "mm", 10.0, (0.0, 2000.0), api_multiplier=1000.0),
    LayerDefinition("gust", "gust", ("wind_gusts_10m",), "gust", "m/s", 100.0, (0.0, 200.0)),
    LayerDefinition("vis", "vis", ("visibility",), "vis", "m", 0.1, (0.0, 100000.0)),
    LayerDefinition(
        "prmsl",
        "prmsl",
        ("pressure_msl",),
        "prmsl",
        "Pa",
        1.0,
        (50000.0, 115000.0),
        vmin=50000.0,
        api_multiplier=100.0,
    ),
)


def compute_gfs013_region_grid(
    *,
    left_lon: float,
    right_lon: float,
    bottom_lat: float,
    top_lat: float,
) -> RegionGrid:
    full_nx = 3072
    full_ny = 1536
    lon_min = -180.0
    dx = 360.0 / float(full_nx)
    dy = 0.11714935
    lat_min = -dy * float(full_ny - 1) / 2.0
    epsilon = 1e-9
    x0 = max(0, int(math.ceil((left_lon - lon_min) / dx - epsilon)))
    x1 = min(full_nx - 1, int(math.floor((right_lon - lon_min) / dx + epsilon)))
    y0 = max(0, int(math.ceil((bottom_lat - lat_min) / dy - epsilon)))
    y1 = min(full_ny - 1, int(math.floor((top_lat - lat_min) / dy + epsilon)))
    if x0 > x1 or y0 > y1:
        raise ValueError("configured region does not overlap GFS013 source grid")

    width = x1 - x0 + 1
    height = y1 - y0 + 1
    region_lon_min = lon_min + float(x0) * dx
    region_lat_min = lat_min + float(y0) * dy
    longitude_values = [round(region_lon_min + float(x) * dx, 6) for x in range(width)]
    latitude_values = [round(region_lat_min + float(y) * dy, 6) for y in range(height)]
    return RegionGrid(
        width=width,
        height=height,
        lat_min=round(region_lat_min, 6),
        lon_min=round(region_lon_min, 6),
        dx=dx,
        dy=dy,
        latitude_values=latitude_values,
        longitude_values=longitude_values,
        row_order="south_to_north",
    )


def round_half_away_from_zero(values: np.ndarray) -> np.ndarray:
    parsed = np.asarray(values, dtype=np.float64)
    return np.where(parsed >= 0.0, np.floor(parsed + 0.5), np.ceil(parsed - 0.5))


def encode_scalar_rgba(data_array: np.ndarray, *, vmin: float = 0.0, scale: float = 100.0) -> np.ndarray:
    data = np.asarray(data_array, dtype=np.float32)
    rgba = np.zeros((data.shape[0], data.shape[1], 4), dtype=np.uint8)
    mask_invalid = ~np.isfinite(data)
    safe_data = np.where(mask_invalid, vmin, data)
    encoded = np.clip(round_half_away_from_zero((safe_data - vmin) * scale), 0, 65535).astype(np.uint16)
    rgba[:, :, 0] = (encoded >> 8).astype(np.uint8)
    rgba[:, :, 1] = (encoded & 0xFF).astype(np.uint8)
    rgba[:, :, 3] = 255
    rgba[mask_invalid, 3] = 0
    return rgba


def decode_scalar_rgba(rgba: np.ndarray, *, vmin: float = 0.0, scale: float = 100.0) -> np.ndarray:
    image = np.asarray(rgba, dtype=np.uint8)
    encoded = image[:, :, 0].astype(np.uint16) * 256 + image[:, :, 1].astype(np.uint16)
    out = vmin + encoded.astype(np.float64) / scale
    out[image[:, :, 3] == 0] = np.nan
    return out


def encode_wind_rgba(u_array: np.ndarray, v_array: np.ndarray) -> np.ndarray:
    u = np.asarray(u_array, dtype=np.float32)
    v = np.asarray(v_array, dtype=np.float32)
    rgba = np.zeros((u.shape[0], u.shape[1], 4), dtype=np.uint8)
    wind_speed = np.sqrt(u**2 + v**2)
    mask_invalid = (
        ~np.isfinite(u)
        | ~np.isfinite(v)
        | (wind_speed > 150.0)
        | (u < -100.0)
        | (u > 100.0)
        | (v < -100.0)
        | (v > 100.0)
    )
    safe_u = np.where(mask_invalid, -100.0, u)
    safe_v = np.where(mask_invalid, -100.0, v)
    u_12 = np.clip(round_half_away_from_zero(safe_u / 0.1) + 1000.0, 0, 4095).astype(np.uint16)
    v_12 = np.clip(round_half_away_from_zero(safe_v / 0.1) + 1000.0, 0, 4095).astype(np.uint16)
    rgba[:, :, 0] = (u_12 >> 4).astype(np.uint8)
    rgba[:, :, 1] = (((u_12 & 0x0F) << 4) | (v_12 >> 8)).astype(np.uint8)
    rgba[:, :, 2] = (v_12 & 0xFF).astype(np.uint8)
    rgba[:, :, 3] = 255
    rgba[mask_invalid] = [0, 0, 0, 0]
    return rgba


def decode_wind_rgba(rgba: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    image = np.asarray(rgba, dtype=np.uint8)
    u_encoded = (image[:, :, 0].astype(np.uint16) << 4) | (image[:, :, 1].astype(np.uint16) >> 4)
    v_encoded = ((image[:, :, 1].astype(np.uint16) & 0x0F) << 8) | image[:, :, 2].astype(np.uint16)
    u = -100.0 + u_encoded.astype(np.float64) / 10.0
    v = -100.0 + v_encoded.astype(np.float64) / 10.0
    mask_invalid = image[:, :, 3] == 0
    u[mask_invalid] = np.nan
    v[mask_invalid] = np.nan
    return u, v


def save_webp_rgba(rgba: np.ndarray, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + f".tmp.{os.getpid()}")
    Image.fromarray(rgba, mode="RGBA").save(tmp_path, "WEBP", quality=100, lossless=True, method=4)
    tmp_path.replace(out_path)


def required_api_variables(layer_definitions: Sequence[LayerDefinition]) -> list[str]:
    seen: set[str] = set()
    variables: list[str] = []
    for layer in layer_definitions:
        for variable in layer.api_variables:
            if variable not in seen:
                seen.add(variable)
                variables.append(variable)
    return variables


def build_forecast_params(
    *,
    latitudes: Sequence[float],
    longitudes: Sequence[float],
    variables: Sequence[str],
    model: str,
    start_hour: str,
    end_hour: str,
) -> dict[str, str]:
    return {
        "latitude": ",".join(f"{value:.6f}" for value in latitudes),
        "longitude": ",".join(f"{value:.6f}" for value in longitudes),
        "hourly": ",".join(variables),
        "models": model,
        "timezone": "UTC",
        "cell_selection": "land",
        "start_hour": start_hour,
        "end_hour": end_hour,
    }


def fetch_forecast_chunk(
    *,
    api_base_url: str,
    latitudes: Sequence[float],
    longitudes: Sequence[float],
    variables: Sequence[str],
    model: str,
    start_hour: str,
    end_hour: str,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    params = build_forecast_params(
        latitudes=latitudes,
        longitudes=longitudes,
        variables=variables,
        model=model,
        start_hour=start_hour,
        end_hour=end_hour,
    )
    response = requests.get(api_base_url, params=params, timeout=timeout_seconds)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        return [payload]
    if not isinstance(payload, list):
        raise ValueError(f"unexpected Open-Meteo response type: {type(payload)!r}")
    return payload


def iter_flat_chunks(total: int, chunk_size: int) -> Iterable[range]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    for start in range(0, total, chunk_size):
        yield range(start, min(start + chunk_size, total))


def parse_utc_hour(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def frame_stems(times: Sequence[str], source_start_hour: str) -> list[str]:
    version = parse_utc_hour(source_start_hour).strftime("%y%m%d%H")
    stems: list[str] = []
    for value in times:
        valid_ts = int(parse_utc_hour(value).timestamp())
        stems.append(f"{valid_ts}_{version}_oml{LAYER_PRODUCT_VERSION}")
    return stems


def selected_layers(names: str | None) -> tuple[LayerDefinition, ...]:
    if not names:
        return DEFAULT_LAYER_DEFINITIONS
    requested = {name.strip() for name in names.split(",") if name.strip()}
    layers = tuple(layer for layer in DEFAULT_LAYER_DEFINITIONS if layer.name in requested)
    missing = requested - {layer.name for layer in layers}
    if missing:
        raise ValueError(f"unknown layer names: {', '.join(sorted(missing))}")
    return layers


def allocate_variable_store(variable_dir: Path, variables: Sequence[str], shape: tuple[int, int, int]) -> dict[str, np.memmap]:
    variable_dir.mkdir(parents=True, exist_ok=True)
    stores: dict[str, np.memmap] = {}
    for variable in variables:
        store = np.memmap(variable_dir / f"{variable}.float32", dtype=np.float32, mode="w+", shape=shape)
        store[:] = np.nan
        stores[variable] = store
    return stores


def fill_variable_store(
    *,
    stores: dict[str, np.memmap],
    response: list[dict[str, Any]],
    flat_indices: Sequence[int],
    variables: Sequence[str],
    expected_times: Sequence[str],
    grid: RegionGrid,
) -> None:
    if len(response) != len(flat_indices):
        raise ValueError(f"response point count mismatch: got {len(response)} expected {len(flat_indices)}")
    for item, flat_index in zip(response, flat_indices):
        hourly = item.get("hourly") or {}
        times = hourly.get("time") or []
        if list(times) != list(expected_times):
            raise ValueError("Open-Meteo response time axis changed between chunks")
        y, x, _lat, _lon = grid.point_for_flat_index(flat_index)
        for variable in variables:
            values = hourly.get(variable)
            if values is None:
                raise ValueError(f"Open-Meteo response missing variable {variable}")
            stores[variable][:, y, x] = np.asarray(values, dtype=np.float32)


def publish_build(build_dir: Path, output_dir: Path, filenames: set[str], manifest: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    subdirs = manifest["subdirs"]
    for subdir in subdirs:
        (output_dir / subdir).mkdir(parents=True, exist_ok=True)
        source_dir = build_dir / subdir
        for filename in sorted(filenames):
            (source_dir / filename).replace(output_dir / subdir / filename)
        for existing in (output_dir / subdir).glob("*.webp"):
            if existing.name not in filenames:
                existing.unlink()
    manifest_path = output_dir / "gfs013_surface_data.json"
    tmp_manifest = manifest_path.with_suffix(manifest_path.suffix + f".tmp.{os.getpid()}")
    tmp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_manifest.replace(manifest_path)


def build_layers(
    *,
    api_base_url: str,
    output_dir: Path,
    model: str,
    start_hour: str,
    end_hour: str,
    left_lon: float,
    right_lon: float,
    bottom_lat: float,
    top_lat: float,
    layer_names: str | None,
    chunk_size: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    grid = compute_gfs013_region_grid(
        left_lon=left_lon,
        right_lon=right_lon,
        bottom_lat=bottom_lat,
        top_lat=top_lat,
    )
    layers = selected_layers(layer_names)
    variables = required_api_variables(layers)
    build_dir = output_dir / f".build_{os.getpid()}_{int(time.time())}"
    variable_dir = build_dir / ".variables"
    build_dir.mkdir(parents=True, exist_ok=True)

    try:
        chunk_iter = iter_flat_chunks(grid.flat_count(), chunk_size)
        first_chunk = next(chunk_iter)
        first_latitudes: list[float] = []
        first_longitudes: list[float] = []
        for flat_index in first_chunk:
            _y, _x, lat, lon = grid.point_for_flat_index(flat_index)
            first_latitudes.append(lat)
            first_longitudes.append(lon)
        first_response = fetch_forecast_chunk(
            api_base_url=api_base_url,
            latitudes=first_latitudes,
            longitudes=first_longitudes,
            variables=variables,
            model=model,
            start_hour=start_hour,
            end_hour=end_hour,
            timeout_seconds=timeout_seconds,
        )
        if not first_response:
            raise RuntimeError("Open-Meteo API returned no locations")
        times = list((first_response[0].get("hourly") or {}).get("time") or [])
        if not times:
            raise RuntimeError("Open-Meteo API returned no hourly time axis")
        stores = allocate_variable_store(variable_dir, variables, (len(times), grid.height, grid.width))
        fill_variable_store(
            stores=stores,
            response=first_response,
            flat_indices=list(first_chunk),
            variables=variables,
            expected_times=times,
            grid=grid,
        )

        completed_points = len(first_response)
        for chunk in chunk_iter:
            latitudes: list[float] = []
            longitudes: list[float] = []
            chunk_indices = list(chunk)
            for flat_index in chunk_indices:
                _y, _x, lat, lon = grid.point_for_flat_index(flat_index)
                latitudes.append(lat)
                longitudes.append(lon)
            response = fetch_forecast_chunk(
                api_base_url=api_base_url,
                latitudes=latitudes,
                longitudes=longitudes,
                variables=variables,
                model=model,
                start_hour=start_hour,
                end_hour=end_hour,
                timeout_seconds=timeout_seconds,
            )
            fill_variable_store(
                stores=stores,
                response=response,
                flat_indices=chunk_indices,
                variables=variables,
                expected_times=times,
                grid=grid,
            )
            completed_points += len(response)
            print(f"[openmeteo-layers] fetched {completed_points}/{grid.flat_count()} grid points", flush=True)

        stems = frame_stems(times, start_hour)
        filenames = {f"{stem}.webp" for stem in stems}
        for layer in layers:
            layer_dir = build_dir / layer.subdir
            layer_dir.mkdir(parents=True, exist_ok=True)
            for time_index, stem in enumerate(stems):
                if layer.data_type == "vector":
                    u = stores[layer.api_variables[0]][time_index]
                    v = stores[layer.api_variables[1]][time_index]
                    rgba = encode_wind_rgba(u, v)
                else:
                    values = np.asarray(stores[layer.api_variables[0]][time_index], dtype=np.float32)
                    values = values * np.float32(layer.api_multiplier)
                    rgba = encode_scalar_rgba(values, vmin=layer.vmin, scale=layer.scale)
                save_webp_rgba(rgba, layer_dir / f"{stem}.webp")
            print(f"[openmeteo-layers] rendered {layer.name} frames={len(stems)}", flush=True)

        start_dt = parse_utc_hour(start_hour)
        manifest = {
            "update_timestamp": int(start_dt.timestamp()),
            "generated_at": int(time.time()),
            "version": LAYER_PRODUCT_VERSION,
            "model": model,
            "file_count": len(stems),
            "grid": grid.manifest(),
            "files": stems,
            "times": times,
            "start_hour": start_hour,
            "end_hour": end_hour,
            "subdirs": [layer.subdir for layer in layers],
            "layers": {layer.name: layer.manifest(grid) for layer in layers},
            "source": "openmeteo_api",
            "api_base_url": api_base_url,
        }
        publish_build(build_dir, output_dir, filenames, manifest)
        return manifest
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build regional WebP weather layers from the local Open-Meteo API.")
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model", default=os.environ.get("WEATHER_OPENMETEO_LAYER_MODEL", "gfs013"))
    parser.add_argument("--start-hour", required=True, help="UTC start hour, for example 2026-06-25T07:00")
    parser.add_argument("--end-hour", required=True, help="UTC included end hour, for example 2026-06-27T08:00")
    parser.add_argument("--left-lon", type=float, default=float(os.environ.get("WEATHER_REGION_LEFT_LON", "70.0")))
    parser.add_argument("--right-lon", type=float, default=float(os.environ.get("WEATHER_REGION_RIGHT_LON", "140.0")))
    parser.add_argument("--bottom-lat", type=float, default=float(os.environ.get("WEATHER_REGION_BOTTOM_LAT", "0.0")))
    parser.add_argument("--top-lat", type=float, default=float(os.environ.get("WEATHER_REGION_TOP_LAT", "58.0")))
    parser.add_argument("--layers", default=None, help="Comma-separated layer names. Defaults to all surface layers.")
    parser.add_argument("--chunk-size", type=int, default=int(os.environ.get("WEATHER_OPENMETEO_LAYER_CHUNK_SIZE", "500")))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.environ.get("WEATHER_OPENMETEO_LAYER_TIMEOUT", "120")))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_layers(
        api_base_url=args.api_base_url,
        output_dir=Path(args.output_dir),
        model=args.model,
        start_hour=args.start_hour,
        end_hour=args.end_hour,
        left_lon=args.left_lon,
        right_lon=args.right_lon,
        bottom_lat=args.bottom_lat,
        top_lat=args.top_lat,
        layer_names=args.layers,
        chunk_size=args.chunk_size,
        timeout_seconds=args.timeout_seconds,
    )
    print(
        "[openmeteo-layers] ready "
        f"model={manifest['model']} frames={manifest['file_count']} "
        f"grid={manifest['grid']['grid_width']}x{manifest['grid']['grid_height']} "
        f"output={Path(args.output_dir)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
