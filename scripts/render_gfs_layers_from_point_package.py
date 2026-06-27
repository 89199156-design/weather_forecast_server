#!/usr/bin/env python3
"""Render GFS WebP layers from the Open-Meteo point package."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_openmeteo_layers as layer_builder  # noqa: E402


DEFAULT_POINT_DIR = Path("./data/openmeteo_points/gfs013_point")
DEFAULT_OUTPUT_DIR = Path("./data/openmeteo_layers/gfs013_surface")


FIELD_BY_API_VARIABLE: dict[str, tuple[str, float]] = {
    "temperature_2m": ("temperature_c", 1.0),
    "dew_point_2m": ("dew_point_c", 1.0),
    "relative_humidity_2m": ("humidity_pct", 1.0),
    "wind_u_component_10m": ("u10_ms", 1.0),
    "wind_v_component_10m": ("v10_ms", 1.0),
    "wind_gusts_10m": ("gust_ms", 1.0),
    "visibility": ("visibility_m", 1.0),
    "precipitation": ("precip_1h_mm", 1.0),
    "snow_depth": ("snow_depth_m", 1.0),
    "weather_code": ("weather_code", 1.0),
    "cape": ("cape_jkg", 1.0),
    "pressure_msl": ("mean_sea_level_pressure_pa", 0.01),
    "surface_pressure": ("surface_pressure_pa", 0.01),
    "cloud_cover": ("cloud_total_pct", 1.0),
    "cloud_cover_low": ("cloud_low_pct", 1.0),
    "cloud_cover_mid": ("cloud_mid_pct", 1.0),
    "cloud_cover_high": ("cloud_high_pct", 1.0),
    "uv_index": ("uv_index", 1.0),
}


def iso_hour_from_timestamp(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M")


def load_meta(point_dir: Path) -> dict[str, Any]:
    return json.loads((point_dir / "point_weather_meta.json").read_text(encoding="utf-8"))


def field_lookup(meta: dict[str, Any]) -> dict[str, tuple[int, float, float, int]]:
    lookup: dict[str, tuple[int, float, float, int]] = {}
    for index, field in enumerate(meta["fields"]):
        lookup[str(field["name"])] = (
            index,
            float(field["scale"]),
            float(field.get("offset", 0.0)),
            int(field.get("missing_value", meta.get("missing_value", -32768))),
        )
    return lookup


def region_grid_from_meta(meta: dict[str, Any]) -> layer_builder.RegionGrid:
    grid = meta["grid"]
    return layer_builder.RegionGrid(
        width=int(grid["grid_width"]),
        height=int(grid["grid_height"]),
        lat_min=float(grid["sample_bounds"]["lat_min"]),
        lon_min=float(grid["sample_bounds"]["lon_min"]),
        dx=float(grid["center_dx"]),
        dy=float(grid["center_dy"]),
        latitude_values=[float(value) for value in grid["latitude_values"]],
        longitude_values=[float(value) for value in grid["longitude_values"]],
        row_order=str(grid.get("row_order", "north_to_south")),
    )


def decode_field(
    package: np.memmap,
    lookup: dict[str, tuple[int, float, float, int]],
    api_variable: str,
    time_index: int,
) -> np.ndarray:
    field_name, multiplier = FIELD_BY_API_VARIABLE[api_variable]
    field_index, scale, offset, missing = lookup[field_name]
    raw = np.asarray(package[:, :, time_index, field_index], dtype=np.int16)
    values = raw.astype(np.float32) * np.float32(scale) + np.float32(offset)
    values[raw == missing] = np.nan
    return values * np.float32(multiplier)


def publish_build(build_dir: Path, output_dir: Path) -> None:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = output_dir.with_name(f".{output_dir.name}.prev.{os.getpid()}")
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    if output_dir.exists():
        output_dir.rename(backup_dir)
    build_dir.rename(output_dir)
    if backup_dir.exists():
        shutil.rmtree(backup_dir)


def render_layers(*, point_dir: Path, output_dir: Path, layer_names: str | None) -> dict[str, Any]:
    meta = load_meta(point_dir)
    grid = region_grid_from_meta(meta)
    lookup = field_lookup(meta)
    layers = layer_builder.selected_layers(layer_names, scope="gfs")
    missing_variables = [
        variable
        for layer in layers
        for variable in layer.api_variables
        if variable not in FIELD_BY_API_VARIABLE
    ]
    if missing_variables:
        raise ValueError(f"point package cannot render variables: {sorted(set(missing_variables))}")

    time_count = int(meta["time_count"])
    timestamps = [int(value) for value in meta["valid_timestamps"]]
    times = [iso_hour_from_timestamp(ts) for ts in timestamps]
    start_hour = str(meta.get("query_window", {}).get("start_hour") or times[0])
    stems = layer_builder.frame_stems(times, start_hour)
    shape = (grid.height, grid.width, time_count, len(meta["fields"]))
    package = np.memmap(point_dir / str(meta.get("bin_file", "point_weather.bin")), dtype=np.int16, mode="r", shape=shape)

    build_dir = output_dir.parent / f".build_layers_from_point_{os.getpid()}_{int(time.time())}"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)
    try:
        for layer in layers:
            layer_dir = build_dir / layer.subdir
            layer_dir.mkdir(parents=True, exist_ok=True)
            for time_index, stem in enumerate(stems):
                if layer.data_type == "vector":
                    u = decode_field(package, lookup, layer.api_variables[0], time_index)
                    v = decode_field(package, lookup, layer.api_variables[1], time_index)
                    rgba = layer_builder.encode_wind_rgba(u, v)
                else:
                    values = decode_field(package, lookup, layer.api_variables[0], time_index)
                    values = layer_builder.derive_layer_values(layer, values)
                    values = values * np.float32(layer.api_multiplier)
                    rgba = layer_builder.encode_scalar_rgba(values, vmin=layer.vmin, scale=layer.scale)
                layer_builder.save_webp_rgba(rgba, layer_dir / f"{stem}.webp")
            print(f"[gfs-layers-from-point] rendered {layer.name} frames={len(stems)}", flush=True)

        manifest = {
            "update_timestamp": int(meta["update_timestamp"]),
            "generated_at": int(time.time()),
            "version": layer_builder.LAYER_PRODUCT_VERSION,
            "scope": "gfs",
            "model": "gfs_global",
            "domain": None,
            "file_count": len(stems),
            "grid": grid.manifest(),
            "files": stems,
            "times": times,
            "start_hour": times[0],
            "end_hour": times[-1],
            "subdirs": [layer.subdir for layer in layers],
            "layers": {layer.name: layer.manifest(grid) for layer in layers},
            "source": "openmeteo_point_package",
            "point_package": {
                "path": str(point_dir),
                "update_timestamp": meta.get("update_timestamp"),
                "field_count": meta.get("field_count"),
                "time_count": meta.get("time_count"),
            },
            "api_base_url": meta.get("api_base_url"),
            "api_host_header": meta.get("api_host_header"),
            "api_options": meta.get("api_options"),
            "run": meta.get("query_window", {}).get("run"),
            "request_forecast_hours": meta.get("query_window", {}).get("request_forecast_hours"),
        }
        (build_dir / layer_builder.manifest_filename_for_scope("gfs")).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        publish_build(build_dir, output_dir)
        print(
            f"[gfs-layers-from-point] ready output={output_dir} frames={len(stems)} layers={len(layers)}",
            flush=True,
        )
        return manifest
    except Exception:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise
    finally:
        del package


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render GFS WebP layers from the Open-Meteo point package.")
    parser.add_argument("--point-dir", default=str(DEFAULT_POINT_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--layers", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    render_layers(
        point_dir=Path(args.point_dir),
        output_dir=Path(args.output_dir),
        layer_names=args.layers,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
