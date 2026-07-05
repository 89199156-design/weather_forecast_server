#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_openmeteo_layers as layer_builder
from validate_openmeteo_point_api import fetch_json, fetch_json_via_ssh


DEFAULT_LAYER_DIR = layer_builder.DEFAULT_OUTPUT_DIR
DEFAULT_API_BASE_URL = layer_builder.DEFAULT_API_BASE_URL
DEFAULT_GFS_MANIFEST = layer_builder.manifest_filename_for_scope("gfs")
DEFAULT_CAMS_MANIFEST = layer_builder.manifest_filename_for_scope("cams")


def evenly_spaced_flat_indices(total: int, max_points: int) -> list[int]:
    if total <= 0:
        return []
    if max_points <= 0 or max_points >= total:
        return list(range(total))
    return [int(round(value)) for value in np.linspace(0, total - 1, max_points)]


def transform_api_value(value: Any, layer: dict[str, Any]) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        return None
    derive = layer.get("derive")
    if derive == "precip_phase_from_weather_code":
        phase = layer_builder.precip_phase_from_weather_code(np.asarray([[parsed]], dtype=np.float32))[0, 0]
        return None if not math.isfinite(float(phase)) else float(phase)
    if derive == "thunderstorm_code_from_weather_code":
        code = layer_builder.thunderstorm_code_from_weather_code(np.asarray([[parsed]], dtype=np.float32))[0, 0]
        return None if not math.isfinite(float(code)) else float(code)
    if derive:
        raise ValueError(f"unknown layer derive transform: {derive}")
    return parsed * float(layer.get("api_multiplier", 1.0))


def values_match(expected: float | None, actual: float | None, *, scale: float) -> bool:
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    tolerance = 0.5 / float(scale) + 1e-6
    return math.isclose(float(expected), float(actual), abs_tol=tolerance)


def grid_index(grid: dict[str, Any], *, lat: float, lon: float) -> tuple[int, int]:
    lat_values = grid.get("latitude_values") or []
    lon_values = grid.get("longitude_values") or []
    if lat_values and lon_values:
        lat_array = np.asarray(lat_values, dtype=np.float64)
        lon_array = np.asarray(lon_values, dtype=np.float64)
        return int(np.nanargmin(np.abs(lat_array - lat))), int(np.nanargmin(np.abs(lon_array - lon)))

    width = grid_width(grid)
    height = grid_height(grid)
    bounds = grid["sample_bounds"]
    south = float(bounds["lat_min"])
    north = float(bounds["lat_max"])
    west = float(bounds["lon_min"])
    dy = grid_dy(grid)
    dx = grid_dx(grid)
    if grid.get("row_order") == "north_to_south":
        y = int(round((north - lat) / dy))
    else:
        y = int(round((lat - south) / dy))
    x = int(round((lon - west) / dx))
    if y < 0 or y >= height or x < 0 or x >= width:
        raise ValueError(f"point outside layer grid: lat={lat} lon={lon}")
    return y, x


def grid_center(grid: dict[str, Any], *, y: int, x: int) -> tuple[float, float]:
    lat_values = grid.get("latitude_values") or []
    lon_values = grid.get("longitude_values") or []
    if lat_values and lon_values:
        return float(lat_values[y]), float(lon_values[x])

    bounds = grid["sample_bounds"]
    south = float(bounds["lat_min"])
    north = float(bounds["lat_max"])
    west = float(bounds["lon_min"])
    dy = grid_dy(grid)
    dx = grid_dx(grid)
    if grid.get("row_order") == "north_to_south":
        return north - y * dy, west + x * dx
    return south + y * dy, west + x * dx


def decode_scalar_pixel(pixel: Sequence[int], *, vmin: float, scale: float) -> float | None:
    if int(pixel[3]) == 0:
        return None
    encoded = int(pixel[0]) * 256 + int(pixel[1])
    return float(vmin) + float(encoded) / float(scale)


def decode_wind_pixel(pixel: Sequence[int]) -> tuple[float | None, float | None]:
    if int(pixel[3]) == 0:
        return None, None
    u_encoded = (int(pixel[0]) << 4) | (int(pixel[1]) >> 4)
    v_encoded = ((int(pixel[1]) & 0x0F) << 8) | int(pixel[2])
    return -100.0 + float(u_encoded) / 10.0, -100.0 + float(v_encoded) / 10.0


@lru_cache(maxsize=1024)
def load_rgba_image(path: str) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"))


def variables_from_manifest(layers: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    variables: list[str] = []
    for layer in layers.values():
        for variable in layer.get("api_variables") or []:
            if variable not in seen:
                seen.add(variable)
                variables.append(variable)
    return variables


def endpoint_for_scope(scope: str) -> str:
    if scope == "gfs":
        return "/v1/forecast"
    if scope == "cams":
        return "/v1/air-quality"
    raise ValueError(f"unknown layer scope: {scope}")


def split_api_base_url(api_base_url: str, scope: str) -> tuple[str, str]:
    endpoint = endpoint_for_scope(scope)
    if api_base_url.rstrip("/").endswith(endpoint):
        return api_base_url.rstrip()[: -len(endpoint)], endpoint
    return api_base_url.rstrip("/"), endpoint


def scope_from_manifest(manifest: dict[str, Any]) -> str:
    return str(manifest.get("scope") or manifest.get("source") or "gfs")


def grid_width(grid: dict[str, Any]) -> int:
    return int(grid.get("grid_width", grid.get("width")))


def grid_height(grid: dict[str, Any]) -> int:
    return int(grid.get("grid_height", grid.get("height")))


def grid_dx(grid: dict[str, Any]) -> float:
    return float(grid.get("center_dx", grid.get("longitude_step", grid.get("dx"))))


def grid_dy(grid: dict[str, Any]) -> float:
    return float(grid.get("center_dy", grid.get("latitude_step", grid.get("dy"))))


def validation_layer_payload(layer: layer_builder.LayerDefinition, *, scope: str) -> dict[str, Any]:
    payload = layer.manifest(source_resolution=layer_builder.layer_resolution_for_layer(scope, layer.name))
    payload["api_variables"] = list(layer.api_variables)
    payload["api_multiplier"] = layer.api_multiplier
    payload["data_type"] = layer.data_type
    if layer.derive is not None:
        payload["derive"] = layer.derive
    return payload


def default_validation_layers(scope: str) -> dict[str, Any]:
    return {
        layer.name: validation_layer_payload(layer, scope=scope)
        for layer in layer_builder.layer_definitions_for_scope(scope)
    }


def layers_for_manifest(manifest: dict[str, Any], layers_filter: str | None) -> dict[str, Any]:
    scope = scope_from_manifest(manifest)
    layers = default_validation_layers(scope)
    for name, layer in (manifest.get("layers") or {}).items():
        merged = dict(layers.get(name, {}))
        merged.update(layer)
        if "data_type" not in merged and merged.get("encoding") == "uv":
            merged["data_type"] = "vector"
        layers[name] = merged
    return selected_layer_items(layers, layers_filter)


def manifest_times(manifest: dict[str, Any]) -> list[str]:
    if manifest.get("times"):
        return [str(value) for value in manifest["times"]]
    files = manifest.get("files") or []
    return [datetime.fromtimestamp(int(value), timezone.utc).strftime("%Y-%m-%dT%H:00") for value in files]


def manifest_start_hour(manifest: dict[str, Any]) -> str:
    if manifest.get("start_hour"):
        return str(manifest["start_hour"])
    times = manifest_times(manifest)
    if not times:
        raise ValueError("manifest has no time axis")
    return times[0]


def manifest_end_hour(manifest: dict[str, Any]) -> str:
    if manifest.get("end_hour"):
        return str(manifest["end_hour"])
    times = manifest_times(manifest)
    if not times:
        raise ValueError("manifest has no time axis")
    return times[-1]


def manifest_api_options(manifest: dict[str, Any]) -> dict[str, str]:
    if "api_options" in manifest:
        return {str(key): str(value) for key, value in (manifest.get("api_options") or {}).items()}
    return layer_builder.layer_api_options_for_scope(scope_from_manifest(manifest))


def build_api_params_for_manifest(
    *,
    manifest: dict[str, Any],
    latitudes: Sequence[float],
    longitudes: Sequence[float],
    variables: Sequence[str],
) -> dict[str, str]:
    scope = scope_from_manifest(manifest)
    params = {
        "latitude": ",".join(str(value) for value in latitudes),
        "longitude": ",".join(str(value) for value in longitudes),
        "hourly": ",".join(variables),
        "timezone": "UTC",
        "timeformat": "iso8601",
    }
    params.update(manifest_api_options(manifest))
    if scope == "gfs":
        params["models"] = str(manifest.get("model") or layer_builder.DEFAULT_LAYER_MODEL)
        if manifest.get("run"):
            params["run"] = str(manifest["run"])
            params["forecast_hours"] = str(manifest.get("request_forecast_hours") or len(manifest_times(manifest)))
        else:
            params["start_hour"] = manifest_start_hour(manifest)
            params["end_hour"] = manifest_end_hour(manifest)
    elif scope == "cams":
        params["domains"] = str(manifest.get("domain") or layer_builder.DEFAULT_CAMS_DOMAIN)
        params["start_date"] = manifest_start_hour(manifest)[:10]
        params["end_date"] = manifest_end_hour(manifest)[:10]
    else:
        raise ValueError(f"unknown layer scope: {scope}")
    return params


def manifest_path_for_layer_dir(layer_dir: Path, manifest_name: str | None) -> Path:
    if manifest_name:
        return layer_dir / manifest_name
    gfs_path = layer_dir / DEFAULT_GFS_MANIFEST
    cams_path = layer_dir / DEFAULT_CAMS_MANIFEST
    if gfs_path.exists():
        return gfs_path
    if cams_path.exists():
        return cams_path
    raise FileNotFoundError(f"missing {DEFAULT_GFS_MANIFEST} or {DEFAULT_CAMS_MANIFEST} in {layer_dir}")


def selected_layer_items(layers: dict[str, Any], names: str | None) -> dict[str, Any]:
    if not names:
        return layers
    requested = {name.strip() for name in names.split(",") if name.strip()}
    selected = {name: layer for name, layer in layers.items() if name in requested}
    missing = requested - set(selected)
    if missing:
        raise ValueError(f"unknown layer names: {', '.join(sorted(missing))}")
    return selected


def point_cases(grid: dict[str, Any], max_points: int) -> list[dict[str, Any]]:
    width = grid_width(grid)
    height = grid_height(grid)
    cases: list[dict[str, Any]] = []
    for flat in evenly_spaced_flat_indices(width * height, max_points):
        y = flat // width
        x = flat - y * width
        lat, lon = grid_center(grid, y=y, x=x)
        cases.append({"flat": flat, "y": y, "x": x, "lat": lat, "lon": lon})
    return cases


def time_indices(times: Sequence[str], max_times: int) -> list[int]:
    return evenly_spaced_flat_indices(len(times), max_times)


def stems_by_time(manifest: dict[str, Any]) -> dict[str, str]:
    times = manifest_times(manifest)
    files = manifest.get("files") or []
    if len(times) != len(files):
        raise ValueError("manifest times/files length mismatch")
    batch = manifest.get("batch")
    stems: dict[str, str] = {}
    for valid_time, item in zip(times, files):
        if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
            if batch is None:
                raise ValueError("numeric manifest files require batch")
            stem = str(manifest.get("file_pattern", "{timestamp}_{batch}.webp"))
            stem = stem.replace("{timestamp}", str(int(item))).replace("{batch}", str(int(batch)))
            if stem.endswith(".webp"):
                stem = stem[:-5]
        else:
            stem = str(item)
            if stem.endswith(".webp"):
                stem = stem[:-5]
        stems[str(valid_time)] = stem
    return stems


def decode_layer_value(layer_dir: Path, layer_name: str, layer: dict[str, Any], stem: str, y: int, x: int) -> Any:
    path = layer_dir / str(layer["subdir"]) / f"{stem}.webp"
    if not path.exists():
        raise FileNotFoundError(path)
    image = load_rgba_image(str(path))
    pixel = image[y, x]
    if layer.get("data_type") == "vector":
        return decode_wind_pixel(pixel)
    return decode_scalar_pixel(pixel, vmin=float(layer.get("vmin", 0.0)), scale=float(layer["scale"]))


def fetch_api_chunk_for_manifest(
    *,
    manifest: dict[str, Any],
    api_base_url: str,
    latitudes: Sequence[float],
    longitudes: Sequence[float],
    variables: Sequence[str],
    timeout_seconds: float,
    api_host_header: str | None,
    reference_ssh_host: str | None,
    request_retries: int,
    request_retry_delay: float,
    request_pause: float,
) -> list[dict[str, Any]]:
    scope = scope_from_manifest(manifest)
    params = build_api_params_for_manifest(
        manifest=manifest,
        latitudes=latitudes,
        longitudes=longitudes,
        variables=variables,
    )
    root_url, endpoint = split_api_base_url(api_base_url, scope)
    payload = (
        fetch_json_via_ssh(
            reference_ssh_host,
            root_url,
            endpoint,
            params,
            host_header=api_host_header,
            timeout=timeout_seconds,
            retries=request_retries,
            retry_delay=request_retry_delay,
            request_pause=request_pause,
        )
        if reference_ssh_host
        else fetch_json(
            root_url,
            endpoint,
            params,
            host_header=api_host_header,
            timeout=timeout_seconds,
            retries=request_retries,
            retry_delay=request_retry_delay,
            request_pause=request_pause,
        )
    )
    if isinstance(payload, dict):
        return [payload]
    if not isinstance(payload, list):
        raise ValueError(f"unexpected Open-Meteo response type: {type(payload)!r}")
    return payload


def verify_layers(
    *,
    layer_dir: Path,
    api_base_url: str,
    manifest_name: str | None = None,
    api_host_header: str | None = None,
    reference_ssh_host: str | None = None,
    max_points: int,
    max_times: int,
    chunk_size: int,
    layers_filter: str | None,
    timeout_seconds: float,
    request_retries: int = 3,
    request_retry_delay: float = 2.0,
    request_pause: float = 0.0,
) -> dict[str, Any]:
    manifest_path = manifest_path_for_layer_dir(layer_dir, manifest_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    grid = manifest["grid"]
    layers = layers_for_manifest(manifest, layers_filter)
    variables = variables_from_manifest(layers)
    api_options = manifest_api_options(manifest)
    points = point_cases(grid, max_points)
    times = manifest_times(manifest)
    selected_time_indices = time_indices(times, max_times)
    stem_by_time = stems_by_time(manifest)

    checked = 0
    mismatches: list[dict[str, Any]] = []
    started = time.time()
    for chunk_start in range(0, len(points), chunk_size):
        chunk = points[chunk_start : chunk_start + chunk_size]
        response = fetch_api_chunk_for_manifest(
            manifest=manifest,
            api_base_url=api_base_url,
            latitudes=[case["lat"] for case in chunk],
            longitudes=[case["lon"] for case in chunk],
            variables=variables,
            timeout_seconds=timeout_seconds,
            api_host_header=api_host_header,
            reference_ssh_host=reference_ssh_host,
            request_retries=request_retries,
            request_retry_delay=request_retry_delay,
            request_pause=request_pause,
        )
        if len(response) != len(chunk):
            raise ValueError(f"API point count mismatch: got {len(response)} expected {len(chunk)}")

        for case, api_item in zip(chunk, response):
            hourly = api_item.get("hourly") or {}
            api_times = hourly.get("time") or []
            for time_index in selected_time_indices:
                valid_time = times[time_index]
                try:
                    api_time_index = api_times.index(valid_time)
                except ValueError as exc:
                    raise ValueError(f"API response missing time {valid_time}") from exc
                stem = stem_by_time[valid_time]
                for layer_name, layer in layers.items():
                    if layer.get("data_type") == "vector":
                        api_variables = layer["api_variables"]
                        expected_u = transform_api_value(hourly[api_variables[0]][api_time_index], layer)
                        expected_v = transform_api_value(hourly[api_variables[1]][api_time_index], layer)
                        actual_u, actual_v = decode_layer_value(
                            layer_dir,
                            layer_name,
                            layer,
                            stem,
                            int(case["y"]),
                            int(case["x"]),
                        )
                        u_ok = values_match(expected_u, actual_u, scale=10.0)
                        v_ok = values_match(expected_v, actual_v, scale=10.0)
                        checked += 2
                        if not u_ok or not v_ok:
                            mismatches.append(
                                {
                                    "point": case,
                                    "time": valid_time,
                                    "layer": layer_name,
                                    "expected_u": expected_u,
                                    "actual_u": actual_u,
                                    "expected_v": expected_v,
                                    "actual_v": actual_v,
                                }
                            )
                    else:
                        api_variable = layer["api_variables"][0]
                        expected = transform_api_value(hourly[api_variable][api_time_index], layer)
                        actual = decode_layer_value(
                            layer_dir,
                            layer_name,
                            layer,
                            stem,
                            int(case["y"]),
                            int(case["x"]),
                        )
                        checked += 1
                        if not values_match(expected, actual, scale=float(layer["scale"])):
                            mismatches.append(
                                {
                                    "point": case,
                                    "time": valid_time,
                                    "layer": layer_name,
                                    "api_variable": api_variable,
                                    "expected": expected,
                                    "actual": actual,
                                    "scale": layer["scale"],
                                }
                            )
        print(f"[openmeteo-layer-verify] checked chunks {min(chunk_start + chunk_size, len(points))}/{len(points)}", flush=True)

    return {
        "layer_dir": str(layer_dir),
        "manifest": str(manifest_path),
        "mode": "api",
        "scope": scope_from_manifest(manifest),
        "api_base_url": api_base_url,
        "api_host_header": api_host_header,
        "reference_ssh_host": reference_ssh_host,
        "model": manifest.get("model"),
        "domain": manifest.get("domain"),
        "api_options": api_options,
        "points": len(points),
        "frames": len(selected_time_indices),
        "layers": list(layers.keys()),
        "checked_values": checked,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:50],
        "elapsed_seconds": round(time.time() - started, 3),
    }


def load_exported_stores(export_dir: Path, variables: Sequence[str]) -> tuple[list[str], dict[str, np.memmap], dict[str, Any]]:
    metadata_path = export_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"missing Open-Meteo export metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("layout") != "point_time":
        raise ValueError(f"unsupported Open-Meteo export layout: {metadata.get('layout')!r}")
    if metadata.get("points") is not None:
        location_count = len(metadata["points"])
    else:
        location_count = int(metadata["width"]) * int(metadata["height"])
    timestamps = [int(value) for value in metadata.get("times") or []]
    if not timestamps:
        raise ValueError("Open-Meteo export has no time axis")
    times = [datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:00") for value in timestamps]
    shape = (location_count, len(times))
    expected_bytes = location_count * len(times) * np.dtype(np.float32).itemsize
    stores: dict[str, np.memmap] = {}
    for variable in variables:
        path = export_dir / f"{variable}.float32"
        if not path.exists():
            raise FileNotFoundError(f"missing Open-Meteo export variable: {path}")
        if path.stat().st_size != expected_bytes:
            raise ValueError(
                f"Open-Meteo export variable {variable} has {path.stat().st_size} bytes, expected {expected_bytes}"
            )
        stores[variable] = np.memmap(path, dtype=np.float32, mode="r", shape=shape)
    return times, stores, metadata


def finite_or_none(value: Any) -> float | None:
    parsed = float(value)
    if not math.isfinite(parsed):
        return None
    return parsed


def transformed_export_scalar(layer: dict[str, Any], raw_value: float) -> float | None:
    value = finite_or_none(raw_value)
    if value is None:
        return None
    derive = layer.get("derive")
    if derive == "precip_phase_from_weather_code":
        value = float(layer_builder.precip_phase_from_weather_code(np.asarray([[value]], dtype=np.float32))[0, 0])
    elif derive == "thunderstorm_code_from_weather_code":
        value = float(layer_builder.thunderstorm_code_from_weather_code(np.asarray([[value]], dtype=np.float32))[0, 0])
    elif derive:
        raise ValueError(f"unknown layer derive transform: {derive}")
    if not math.isfinite(value):
        return None
    return value * float(layer.get("api_multiplier", 1.0))


def verify_layers_against_export(
    *,
    layer_dir: Path,
    export_dir: Path,
    manifest_name: str | None = None,
    max_points: int,
    max_times: int,
    layers_filter: str | None,
) -> dict[str, Any]:
    manifest_path = manifest_path_for_layer_dir(layer_dir, manifest_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    grid = manifest["grid"]
    layers = layers_for_manifest(manifest, layers_filter)
    variables = variables_from_manifest(layers)
    points = point_cases(grid, max_points)
    times = manifest_times(manifest)
    selected_time_indices = time_indices(times, max_times)
    stem_by_time = stems_by_time(manifest)
    export_times, stores, export_metadata = load_exported_stores(export_dir, variables)
    export_time_index = {value: index for index, value in enumerate(export_times)}
    point_export = export_metadata.get("points") is not None
    if point_export and len(export_metadata["points"]) != len(points):
        raise ValueError(f"Open-Meteo point export has {len(export_metadata['points'])} points, expected {len(points)}")

    checked = 0
    mismatches: list[dict[str, Any]] = []
    started = time.time()
    for point_ordinal, case in enumerate(points):
        store_location = point_ordinal if point_export else int(case["flat"])
        if point_export:
            exported_point = export_metadata["points"][point_ordinal]
            if not math.isclose(float(exported_point["latitude"]), float(case["lat"]), abs_tol=1e-4):
                raise ValueError(f"point export latitude mismatch at {point_ordinal}")
            if not math.isclose(float(exported_point["longitude"]), float(case["lon"]), abs_tol=1e-4):
                raise ValueError(f"point export longitude mismatch at {point_ordinal}")
        for time_index in selected_time_indices:
            valid_time = times[time_index]
            if valid_time not in export_time_index:
                raise ValueError(f"Open-Meteo export missing time {valid_time}")
            exported_index = export_time_index[valid_time]
            stem = stem_by_time[valid_time]
            for layer_name, layer in layers.items():
                if layer.get("data_type") == "vector":
                    api_variables = layer["api_variables"]
                    expected_u = finite_or_none(stores[api_variables[0]][store_location, exported_index])
                    expected_v = finite_or_none(stores[api_variables[1]][store_location, exported_index])
                    actual_u, actual_v = decode_layer_value(
                        layer_dir,
                        layer_name,
                        layer,
                        stem,
                        int(case["y"]),
                        int(case["x"]),
                    )
                    u_ok = values_match(expected_u, actual_u, scale=10.0)
                    v_ok = values_match(expected_v, actual_v, scale=10.0)
                    checked += 2
                    if not u_ok or not v_ok:
                        mismatches.append(
                            {
                                "point": case,
                                "time": valid_time,
                                "layer": layer_name,
                                "expected_u": expected_u,
                                "actual_u": actual_u,
                                "expected_v": expected_v,
                                "actual_v": actual_v,
                            }
                        )
                else:
                    api_variable = layer["api_variables"][0]
                    expected = transformed_export_scalar(layer, stores[api_variable][store_location, exported_index])
                    actual = decode_layer_value(
                        layer_dir,
                        layer_name,
                        layer,
                        stem,
                        int(case["y"]),
                        int(case["x"]),
                    )
                    checked += 1
                    if not values_match(expected, actual, scale=float(layer["scale"])):
                        mismatches.append(
                            {
                                "point": case,
                                "time": valid_time,
                                "layer": layer_name,
                                "api_variable": api_variable,
                                "expected": expected,
                                "actual": actual,
                                "scale": layer["scale"],
                            }
                        )
        if checked and checked % 100000 == 0:
            print(f"[openmeteo-layer-verify] checked_values={checked}", flush=True)

    return {
        "layer_dir": str(layer_dir),
        "manifest": str(manifest_path),
        "mode": "export",
        "export_dir": str(export_dir),
        "scope": scope_from_manifest(manifest),
        "api_base_url": None,
        "api_host_header": None,
        "reference_ssh_host": None,
        "model": manifest.get("model"),
        "domain": manifest.get("domain"),
        "api_options": manifest_api_options(manifest),
        "points": len(points),
        "frames": len(selected_time_indices),
        "layers": list(layers.keys()),
        "checked_values": checked,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:50],
        "elapsed_seconds": round(time.time() - started, 3),
    }


def point_export_request_payload(
    *,
    layer_dir: Path,
    manifest_name: str | None,
    max_points: int,
    layers_filter: str | None,
) -> dict[str, Any]:
    manifest_path = manifest_path_for_layer_dir(layer_dir, manifest_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scope = scope_from_manifest(manifest)
    layers = layers_for_manifest(manifest, layers_filter)
    variables = variables_from_manifest(layers)
    points = point_cases(manifest["grid"], max_points)
    if scope == "gfs":
        model = str(manifest.get("model") or layer_builder.DEFAULT_LAYER_MODEL)
    elif scope == "cams":
        model = str(manifest.get("domain") or layer_builder.DEFAULT_CAMS_DOMAIN)
    else:
        raise ValueError(f"unknown layer scope: {scope}")
    return {
        "scope": scope,
        "model": model,
        "run": manifest.get("run"),
        "start_hour": manifest_start_hour(manifest),
        "end_hour": manifest_end_hour(manifest),
        "points": [{"latitude": case["lat"], "longitude": case["lon"]} for case in points],
        "variables": variables,
    }


def write_point_export_request(
    *,
    layer_dir: Path,
    manifest_name: str | None,
    max_points: int,
    layers_filter: str | None,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = point_export_request_payload(
        layer_dir=layer_dir,
        manifest_name=manifest_name,
        max_points=max_points,
        layers_filter=layers_filter,
    )
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(report: dict[str, Any], report_path: Path) -> None:
    lines = [
        "# Open-Meteo Layer Validation",
        "",
        f"- layer_dir: `{report['layer_dir']}`",
        f"- manifest: `{report['manifest']}`",
        f"- mode: `{report.get('mode')}`",
        f"- export_dir: `{report.get('export_dir')}`",
        f"- scope: `{report['scope']}`",
        f"- api_base_url: `{report['api_base_url']}`",
        f"- api_host_header: `{report.get('api_host_header')}`",
        f"- reference_ssh_host: `{report.get('reference_ssh_host')}`",
        f"- model: `{report['model']}`",
        f"- domain: `{report.get('domain')}`",
        f"- api_options: `{json.dumps(report.get('api_options') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- points: {report['points']}",
        f"- frames: {report['frames']}",
        f"- layers: {', '.join(report['layers'])}",
        f"- checked_values: {report['checked_values']}",
        f"- mismatch_count: {report['mismatch_count']}",
        f"- elapsed_seconds: {report['elapsed_seconds']}",
        "",
    ]
    if report["mismatches"]:
        lines.append("## First Mismatches")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(report["mismatches"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Open-Meteo WebP layers against API or direct OM export.")
    parser.add_argument("--layer-dir", default=DEFAULT_LAYER_DIR)
    parser.add_argument("--manifest-name")
    parser.add_argument("--prepare-point-export-request", help="Write export-point-forecast request JSON and exit.")
    parser.add_argument("--export-dir", help="Directory from export-layer-grid; when set, compare WebP directly to OM export.")
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--api-host-header")
    parser.add_argument("--reference-ssh-host")
    parser.add_argument("--max-points", type=int, required=True)
    parser.add_argument("--max-times", type=int, default=50)
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--layers", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--request-retries", type=int, default=3)
    parser.add_argument("--request-retry-delay", type=float, default=2.0)
    parser.add_argument("--request-pause", type=float, default=0.0)
    parser.add_argument("--report", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.prepare_point_export_request:
        write_point_export_request(
            layer_dir=Path(args.layer_dir),
            manifest_name=args.manifest_name,
            max_points=args.max_points,
            layers_filter=args.layers,
            output_path=Path(args.prepare_point_export_request),
        )
        return
    if args.export_dir:
        report = verify_layers_against_export(
            layer_dir=Path(args.layer_dir),
            export_dir=Path(args.export_dir),
            manifest_name=args.manifest_name,
            max_points=args.max_points,
            max_times=args.max_times,
            layers_filter=args.layers,
        )
    else:
        report = verify_layers(
            layer_dir=Path(args.layer_dir),
            api_base_url=args.api_base_url,
            manifest_name=args.manifest_name,
            api_host_header=args.api_host_header,
            reference_ssh_host=args.reference_ssh_host,
            max_points=args.max_points,
            max_times=args.max_times,
            chunk_size=args.chunk_size,
            layers_filter=args.layers,
            timeout_seconds=args.timeout_seconds,
            request_retries=args.request_retries,
            request_retry_delay=args.request_retry_delay,
            request_pause=args.request_pause,
        )
    if args.report:
        write_report(report, Path(args.report))
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if report["mismatch_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
