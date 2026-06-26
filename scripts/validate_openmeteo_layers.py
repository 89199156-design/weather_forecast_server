#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import time
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

    width = int(grid["grid_width"])
    height = int(grid["grid_height"])
    bounds = grid["sample_bounds"]
    south = float(bounds["lat_min"])
    west = float(bounds["lon_min"])
    dy = float(grid.get("center_dy", grid.get("latitude_step")))
    dx = float(grid.get("center_dx", grid.get("longitude_step")))
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
    west = float(bounds["lon_min"])
    dy = float(grid.get("center_dy", grid.get("latitude_step")))
    dx = float(grid.get("center_dx", grid.get("longitude_step")))
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
    width = int(grid["grid_width"])
    height = int(grid["grid_height"])
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
    times = manifest.get("times") or []
    files = manifest.get("files") or []
    if len(times) != len(files):
        raise ValueError("manifest times/files length mismatch")
    return {str(t): str(stem) for t, stem in zip(times, files)}


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
    scope = str(manifest.get("scope", "gfs"))
    params = layer_builder.build_layer_api_params(
        scope=scope,
        latitudes=latitudes,
        longitudes=longitudes,
        variables=variables,
        model=str(manifest.get("model", layer_builder.DEFAULT_LAYER_MODEL)),
        domain=manifest.get("domain"),
        start_hour=str(manifest["start_hour"]),
        end_hour=str(manifest["end_hour"]),
        api_options={str(key): str(value) for key, value in (manifest.get("api_options") or {}).items()},
        run=manifest.get("run"),
        request_forecast_hours=manifest.get("request_forecast_hours"),
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
    layers = selected_layer_items(manifest["layers"], layers_filter)
    variables = variables_from_manifest(layers)
    manifest_api_options = manifest["api_options"] if "api_options" in manifest else layer_builder.LAYER_API_OPTIONS
    api_options = {str(key): str(value) for key, value in manifest_api_options.items()}
    points = point_cases(grid, max_points)
    selected_time_indices = time_indices(manifest.get("times") or [], max_times)
    selected_times = [manifest["times"][idx] for idx in selected_time_indices]
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
                valid_time = manifest["times"][time_index]
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
        "scope": manifest.get("scope", "gfs"),
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


def write_report(report: dict[str, Any], report_path: Path) -> None:
    lines = [
        "# Open-Meteo Layer Validation",
        "",
        f"- layer_dir: `{report['layer_dir']}`",
        f"- manifest: `{report['manifest']}`",
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
    parser = argparse.ArgumentParser(description="Validate Open-Meteo API-backed WebP layers against the local API.")
    parser.add_argument("--layer-dir", default=DEFAULT_LAYER_DIR)
    parser.add_argument("--manifest-name")
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
