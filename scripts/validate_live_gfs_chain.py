#!/usr/bin/env python3
"""Validate live Shanghai GFS layers and point API against Singapore Open-Meteo."""

from __future__ import annotations

import argparse
import io
import json
import math
import shlex
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_openmeteo_layers as layer_builder  # noqa: E402
from validate_openmeteo_layers import transform_api_value, values_match  # noqa: E402


DEFAULT_LAYER_BASE_URL = "http://81.69.253.110/data/gfs013_surface"
DEFAULT_POINT_API_URL = "http://81.69.253.110/api/weather/points"
DEFAULT_REFERENCE_API_URL = "https://single-runs-api.open-meteo.com/v1/forecast"


POINT_FIELD_CHECKS: tuple[
    tuple[str, str, Callable[[Any], float | int | None], float],
    ...,
] = (
    ("temperature_2m", "tempC", lambda v: _float(v), 0.11),
    ("apparent_temperature", "apparentTempC", lambda v: _float(v), 0.11),
    ("dew_point_2m", "dewPointC", lambda v: _float(v), 0.11),
    ("relative_humidity_2m", "humidityPct", lambda v: _float(v), 0.51),
    ("wind_u_component_10m", "windU10Ms", lambda v: _float(v), 0.11),
    ("wind_v_component_10m", "windV10Ms", lambda v: _float(v), 0.11),
    ("wind_speed_10m", "openMeteoWindSpeed10mMs", lambda v: _float(v), 0.02),
    ("wind_direction_10m", "openMeteoWindDirection10mDeg", lambda v: _float(v), 0.51),
    ("wind_gusts_10m", "gustMs", lambda v: _float(v), 0.11),
    ("visibility", "visibilityM", lambda v: _float(v), 20.1),
    ("surface_pressure", "surfacePressurePa", lambda v: _float(v) * 100.0 if v is not None else None, 5.1),
    ("pressure_msl", "seaLevelPressurePa", lambda v: _float(v) * 100.0 if v is not None else None, 10.1),
    ("precipitation", "precip1hMm", lambda v: _float(v), 0.11),
    ("rain", "rain1hMm", lambda v: _float(v), 0.11),
    ("showers", "showers1hMm", lambda v: _float(v), 0.11),
    ("snowfall", "snowfallCm", lambda v: _float(v), 0.11),
    ("snow_depth", "snowDepthM", lambda v: _float(v), 0.011),
    ("cloud_cover", "cloudTotalPct", lambda v: _float(v), 0.51),
    ("cloud_cover_low", "cloudLowPct", lambda v: _float(v), 0.51),
    ("cloud_cover_mid", "cloudMidPct", lambda v: _float(v), 0.51),
    ("cloud_cover_high", "cloudHighPct", lambda v: _float(v), 0.51),
    ("cape", "capeJkg", lambda v: _float(v), 10.1),
    ("uv_index", "uvIndex", lambda v: _float(v), 0.011),
    ("uv_index_clear_sky", "uvIndexClearSky", lambda v: _float(v), 0.011),
    ("is_day", "isDay", lambda v: _float(v), 0.0),
    ("weather_code", "weatherCode", lambda v: _float(v), 0.0),
)


def _float(value: Any) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def fetch_url(url: str, *, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def fetch_json_url(url: str, *, timeout: float) -> Any:
    return json.loads(fetch_url(url, timeout=timeout).decode("utf-8"))


def fetch_reference_json_via_ssh(host: str, url: str, *, timeout: float) -> Any:
    code = (
        "import json,sys,urllib.request;"
        "url=sys.stdin.read().strip();"
        f"r=urllib.request.urlopen(url, timeout={float(timeout)!r});"
        "sys.stdout.write(r.read().decode('utf-8'))"
    )
    proc = subprocess.run(
        ["ssh", host, f"python3 -c {shlex.quote(code)}"],
        input=url,
        text=True,
        capture_output=True,
        timeout=timeout + 20.0,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return json.loads(proc.stdout)


def post_json(url: str, payload: Any, *, timeout: float) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def utc_timestamp(hour: str) -> int:
    return int(datetime.fromisoformat(hour).replace(tzinfo=timezone.utc).timestamp())


def selected_cases(grid: dict[str, Any], count: int) -> list[dict[str, Any]]:
    width = int(grid["grid_width"])
    height = int(grid["grid_height"])
    lat_values = np.asarray(grid["latitude_values"], dtype=np.float64)
    lon_values = np.asarray(grid["longitude_values"], dtype=np.float64)
    flats = np.linspace(0, width * height - 1, count, dtype=np.int64)
    cases = []
    for index, flat in enumerate(flats.tolist()):
        y = flat // width
        x = flat - y * width
        cases.append(
            {
                "index": index,
                "flat": int(flat),
                "y": int(y),
                "x": int(x),
                "lat": float(lat_values[y]),
                "lon": float(lon_values[x]),
            }
        )
    return cases


def layer_stems(manifest: dict[str, Any], frame_count: int) -> list[tuple[str, str, int]]:
    out = []
    for frame, (hour, stem) in enumerate(zip(manifest["times"], manifest["files"])):
        if frame >= frame_count:
            break
        out.append((str(hour), str(stem), utc_timestamp(str(hour))))
    return out


def decode_layer_image(layer_base_url: str, subdir: str, stem: str, *, timeout: float) -> np.ndarray:
    url = f"{layer_base_url.rstrip('/')}/{subdir}/{stem}.webp"
    return np.asarray(Image.open(io.BytesIO(fetch_url(url, timeout=timeout))).convert("RGBA"))


def decoded_scalar_at(image: np.ndarray, layer: dict[str, Any], y: int, x: int) -> float | None:
    pixel = image[y, x]
    if int(pixel[3]) == 0:
        return None
    encoded = int(pixel[0]) * 256 + int(pixel[1])
    return float(layer.get("vmin", 0.0)) + encoded / float(layer["scale"])


def decoded_wind_at(image: np.ndarray, y: int, x: int) -> tuple[float | None, float | None]:
    pixel = image[y, x]
    if int(pixel[3]) == 0:
        return None, None
    u_encoded = (int(pixel[0]) << 4) | (int(pixel[1]) >> 4)
    v_encoded = ((int(pixel[1]) & 0x0F) << 8) | int(pixel[2])
    return -100.0 + u_encoded / 10.0, -100.0 + v_encoded / 10.0


def fetch_reference(
    *,
    api_url: str,
    ssh_host: str,
    manifest: dict[str, Any],
    cases: list[dict[str, Any]],
    variables: list[str],
    timeout: float,
) -> list[dict[str, Any]]:
    params = layer_builder.build_layer_api_params(
        scope="gfs",
        latitudes=[case["lat"] for case in cases],
        longitudes=[case["lon"] for case in cases],
        variables=variables,
        model=str(manifest["model"]),
        domain=manifest.get("domain"),
        start_hour=str(manifest["times"][0]),
        end_hour=str(manifest["times"][23]),
        api_options={str(k): str(v) for k, v in (manifest.get("api_options") or {}).items()},
        run=manifest.get("run"),
        request_forecast_hours=manifest.get("request_forecast_hours"),
    )
    url = api_url + "?" + urllib.parse.urlencode(params)
    payload = fetch_reference_json_via_ssh(ssh_host, url, timeout=timeout) if ssh_host else fetch_json_url(url, timeout=timeout)
    return payload if isinstance(payload, list) else [payload]


def compare_close(expected: Any, actual: Any, tolerance: float) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    expected_f = float(expected)
    actual_f = float(actual)
    if tolerance == 0.0:
        return int(round(expected_f)) == int(round(actual_f))
    return math.isclose(expected_f, actual_f, abs_tol=tolerance)


def add_mismatch(mismatches: list[dict[str, Any]], *, limit: int, item: dict[str, Any]) -> int:
    if len(mismatches) < limit:
        mismatches.append(item)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layer-base-url", default=DEFAULT_LAYER_BASE_URL)
    parser.add_argument("--point-api-url", default=DEFAULT_POINT_API_URL)
    parser.add_argument("--reference-api-url", default=DEFAULT_REFERENCE_API_URL)
    parser.add_argument("--reference-ssh-host", default="singapore")
    parser.add_argument("--points", type=int, default=100)
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-examples", type=int, default=50)
    args = parser.parse_args()

    manifest = fetch_json_url(f"{args.layer_base_url.rstrip()}/gfs013_surface_data.json", timeout=args.timeout)
    frames = layer_stems(manifest, args.frames)
    if len(frames) != args.frames:
        raise RuntimeError(f"manifest only has {len(frames)} frames")
    cases = selected_cases(manifest["grid"], args.points)
    variables = sorted(
        {
            variable
            for layer in manifest["layers"].values()
            for variable in layer.get("api_variables", [])
        }
        | {name for name, *_ in POINT_FIELD_CHECKS}
        | {"uv_index_clear_sky", "apparent_temperature", "dew_point_2m", "rain", "showers", "snowfall", "snow_depth", "is_day"}
    )

    reference = fetch_reference(
        api_url=args.reference_api_url,
        ssh_host=args.reference_ssh_host,
        manifest=manifest,
        cases=cases,
        variables=variables,
        timeout=args.timeout,
    )
    if len(reference) != len(cases):
        raise RuntimeError(f"reference returned {len(reference)} points for {len(cases)} cases")

    point_payload = {
        "target_ts": frames[0][2],
        "future_hours": args.frames - 1,
        "past_hours": 0,
        "includeAdvanced": True,
        "points": [
            {
                "index": case["index"],
                "lat": case["lat"],
                "lon": case["lon"],
            }
            for case in cases
        ],
    }
    point_response = post_json(args.point_api_url, point_payload, timeout=args.timeout)
    point_items = sorted(point_response["data"]["points"], key=lambda item: item["index"])
    if len(point_items) != len(cases):
        raise RuntimeError(f"point API returned {len(point_items)} points for {len(cases)} cases")

    mismatches: list[dict[str, Any]] = []
    mismatch_count = 0
    point_checks = 0
    layer_checks = 0
    point_update_timestamps = sorted({item["data"]["updateTimestamp"] for item in point_items if item.get("data")})

    for case_index, (case, ref, point_item) in enumerate(zip(cases, reference, point_items)):
        point_data = point_item.get("data")
        if not point_data:
            mismatch_count += add_mismatch(
                mismatches,
                limit=args.max_examples,
                item={"type": "point_error", "case": case_index, "error": point_item.get("error")},
            )
            continue
        hourly = point_data["hourly"]
        if len(hourly) != args.frames:
            mismatch_count += add_mismatch(
                mismatches,
                limit=args.max_examples,
                item={"type": "point_length", "case": case_index, "actual": len(hourly), "expected": args.frames},
            )
            continue
        ref_hourly = ref["hourly"]
        for frame, (hour, _stem, ts) in enumerate(frames):
            if hourly[frame]["ts"] != ts:
                mismatch_count += add_mismatch(
                    mismatches,
                    limit=args.max_examples,
                    item={"type": "time", "case": case_index, "frame": frame, "actual": hourly[frame]["ts"], "expected": ts},
                )
            ref_index = ref_hourly["time"].index(hour)
            point_frame = hourly[frame]
            for api_name, point_name, transform, tolerance in POINT_FIELD_CHECKS:
                expected = transform(ref_hourly[api_name][ref_index])
                actual = point_frame.get(point_name)
                point_checks += 1
                if not compare_close(expected, actual, tolerance):
                    mismatch_count += add_mismatch(
                        mismatches,
                        limit=args.max_examples,
                        item={
                            "type": "point_api",
                            "case": case_index,
                            "frame": frame,
                            "lat": case["lat"],
                            "lon": case["lon"],
                            "field": point_name,
                            "api": api_name,
                            "expected": expected,
                            "actual": actual,
                        },
                    )
            weather_code = ref_hourly["weather_code"][ref_index]
            phase = float(layer_builder.precip_phase_from_weather_code(np.asarray([[weather_code]], dtype=np.float32))[0, 0])
            thunder = float(layer_builder.thunderstorm_code_from_weather_code(np.asarray([[weather_code]], dtype=np.float32))[0, 0])
            for name, expected in (("precipPhaseCode", phase), ("thunderstormCode", thunder)):
                point_checks += 1
                if not compare_close(expected, point_frame.get(name), 0.0):
                    mismatch_count += add_mismatch(
                        mismatches,
                        limit=args.max_examples,
                        item={
                            "type": "point_derived",
                            "case": case_index,
                            "frame": frame,
                            "field": name,
                            "weather_code": weather_code,
                            "expected": expected,
                            "actual": point_frame.get(name),
                        },
                    )

    for frame, (hour, stem, _ts) in enumerate(frames):
        for layer_name, layer in manifest["layers"].items():
            image = decode_layer_image(args.layer_base_url, str(layer["subdir"]), stem, timeout=args.timeout)
            for case_index, (case, ref) in enumerate(zip(cases, reference)):
                ref_index = ref["hourly"]["time"].index(hour)
                if layer.get("data_type") == "vector":
                    actual_u, actual_v = decoded_wind_at(image, case["y"], case["x"])
                    for component, actual, api_name in (
                        ("u", actual_u, "wind_u_component_10m"),
                        ("v", actual_v, "wind_v_component_10m"),
                    ):
                        expected = ref["hourly"][api_name][ref_index]
                        layer_checks += 1
                        if not compare_close(expected, actual, 0.051):
                            mismatch_count += add_mismatch(
                                mismatches,
                                limit=args.max_examples,
                                item={
                                    "type": "layer_api",
                                    "case": case_index,
                                    "frame": frame,
                                    "layer": f"{layer_name}.{component}",
                                    "expected": expected,
                                    "actual": actual,
                                },
                            )
                    continue
                actual = decoded_scalar_at(image, layer, case["y"], case["x"])
                expected = transform_api_value(ref["hourly"][layer["api_variables"][0]][ref_index], layer)
                layer_checks += 1
                if not values_match(expected, actual, scale=float(layer["scale"])):
                    mismatch_count += add_mismatch(
                        mismatches,
                        limit=args.max_examples,
                        item={
                            "type": "layer_api",
                            "case": case_index,
                            "frame": frame,
                            "layer": layer_name,
                            "expected": expected,
                            "actual": actual,
                        },
                    )

    summary = {
        "layer_base_url": args.layer_base_url,
        "point_api_url": args.point_api_url,
        "reference_api_url": args.reference_api_url,
        "reference_ssh_host": args.reference_ssh_host,
        "points": len(cases),
        "frames": len(frames),
        "point_checks": point_checks,
        "layer_checks": layer_checks,
        "mismatch_count": mismatch_count,
        "mismatch_examples": mismatches,
        "point_update_timestamps": point_update_timestamps,
        "manifest_window": {"start": manifest["start_hour"], "end": manifest["end_hour"]},
        "layers": list(manifest["layers"].keys()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if mismatch_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
