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
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import requests
from PIL import Image


DEFAULT_GFS_API_BASE_URL = os.environ.get("WEATHER_OPENMETEO_GFS_API_URL", "http://127.0.0.1:18080/v1/forecast")
DEFAULT_CAMS_API_BASE_URL = os.environ.get(
    "WEATHER_OPENMETEO_CAMS_API_URL",
    "http://127.0.0.1:18080/v1/air-quality",
)
DEFAULT_ECMWF_API_BASE_URL = os.environ.get(
    "WEATHER_OPENMETEO_ECMWF_API_URL",
    "http://127.0.0.1:18081/v1/ecmwf",
)
DEFAULT_API_BASE_URL = os.environ.get("WEATHER_OPENMETEO_API_URL", DEFAULT_GFS_API_BASE_URL)
DEFAULT_GFS_OUTPUT_DIR = os.environ.get(
    "WEATHER_OPENMETEO_LAYER_DIR",
    "./data/webp/gfs013_surface",
)
DEFAULT_CAMS_OUTPUT_DIR = os.environ.get(
    "WEATHER_OPENMETEO_CAMS_LAYER_DIR",
    "./data/webp/cams_global",
)
DEFAULT_ECMWF_OUTPUT_DIR = os.environ.get(
    "WEATHER_OPENMETEO_ECMWF_LAYER_DIR",
    "./data/webp/ecmwf_ifs025",
)
DEFAULT_OUTPUT_DIR = DEFAULT_GFS_OUTPUT_DIR
DEFAULT_LAYER_MODEL = os.environ.get("WEATHER_OPENMETEO_LAYER_MODEL", "gfs_global")
DEFAULT_CAMS_DOMAIN = os.environ.get("WEATHER_OPENMETEO_CAMS_LAYER_DOMAIN", "cams_global")
DEFAULT_ECMWF_MODEL = os.environ.get(
    "WEATHER_OPENMETEO_ECMWF_LAYER_MODEL",
    "ecmwf_ifs025",
)
GFS_LAYER_API_OPTIONS: dict[str, str] = {
    "wind_speed_unit": "ms",
}
CAMS_LAYER_API_OPTIONS: dict[str, str] = {}
ECMWF_LAYER_API_OPTIONS: dict[str, str] = {
    "wind_speed_unit": "ms",
}
LAYER_API_OPTIONS = GFS_LAYER_API_OPTIONS


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
        def rounded(value: float) -> float:
            return round(float(value), 6)

        lon_min = min(self.longitude_values)
        lon_max = max(self.longitude_values)
        lat_min = min(self.latitude_values)
        lat_max = max(self.latitude_values)
        return {
            "width": self.width,
            "height": self.height,
            "row_order": self.row_order,
            "dx": rounded(self.dx),
            "dy": rounded(self.dy),
            "sample_bounds": {
                "lon_min": rounded(lon_min),
                "lat_min": rounded(lat_min),
                "lon_max": rounded(lon_max),
                "lat_max": rounded(lat_max),
            },
            "display_bounds": {
                "lon_min": rounded(lon_min - self.dx / 2.0),
                "lat_min": rounded(lat_min - self.dy / 2.0),
                "lon_max": rounded(lon_max + self.dx / 2.0),
                "lat_max": rounded(lat_max + self.dy / 2.0),
            },
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
    derive: str | None = None
    interpolation: str = "linear"

    def manifest(self, grid: RegionGrid | None = None, *, source_resolution: str | None = None) -> dict[str, Any]:
        _ = grid
        payload = {
            "subdir": self.subdir,
            "unit": self.unit,
            "encoding": layer_encoding(self),
            "scale": self.scale,
            "vmin": self.vmin,
            "range": list(self.value_range),
        }
        if source_resolution is not None:
            payload["source_resolution"] = source_resolution
        return payload


DEFAULT_LAYER_DEFINITIONS: tuple[LayerDefinition, ...] = (
    LayerDefinition("cloud_total_1", "cloud_total_1", ("cloud_cover",), "tcc", "%", 100.0, (0.0, 100.0)),
    LayerDefinition("cloud_high_1", "cloud_high_1", ("cloud_cover_high",), "hcc", "%", 100.0, (0.0, 100.0)),
    LayerDefinition("cloud_mid_1", "cloud_mid_1", ("cloud_cover_mid",), "mcc", "%", 100.0, (0.0, 100.0)),
    LayerDefinition("cloud_low_1", "cloud_low_1", ("cloud_cover_low",), "lcc", "%", 100.0, (0.0, 100.0)),
    LayerDefinition("t2m", "t2m", ("temperature_2m",), "t2m", "C", 100.0, (-100.0, 100.0), vmin=-100.0),
    LayerDefinition("d2m", "d2m", ("dew_point_2m",), "d2m", "C", 100.0, (-100.0, 100.0), vmin=-100.0),
    LayerDefinition("r2", "r2", ("relative_humidity_2m",), "r2", "%", 100.0, (0.0, 100.0)),
    LayerDefinition(
        "wind",
        "wind",
        ("wind_u_component_10m", "wind_v_component_10m"),
        "wind",
        "m/s",
        10.0,
        (-100.0, 100.0),
        vmin=-100.0,
        data_type="vector",
    ),
    LayerDefinition("tp", "tp", ("precipitation",), "tp", "mm", 100.0, (0.0, 600.0)),
    LayerDefinition("snod", "snod", ("snow_depth",), "snod", "mm", 10.0, (0.0, 2000.0), api_multiplier=1000.0),
    LayerDefinition("gust", "gust", ("wind_gusts_10m",), "gust", "m/s", 100.0, (0.0, 200.0)),
    LayerDefinition("vis", "vis", ("visibility",), "vis", "m", 0.1, (0.0, 100000.0)),
    LayerDefinition(
        "precip_phase",
        "precip_phase",
        ("weather_code",),
        "precip_phase",
        "code",
        1.0,
        (0.0, 4.0),
        data_type="categorical",
        derive="precip_phase_from_weather_code",
        interpolation="nearest",
    ),
    LayerDefinition(
        "thunderstorm_code",
        "thunderstorm_code",
        ("weather_code",),
        "thunderstorm_code",
        "wmo code",
        1.0,
        (0.0, 100.0),
        data_type="categorical",
        derive="thunderstorm_code_from_weather_code",
        interpolation="nearest",
    ),
    LayerDefinition("cape", "cape", ("cape",), "cape", "J/kg", 1.0, (0.0, 65535.0)),
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
    LayerDefinition(
        "sp",
        "sp",
        ("surface_pressure",),
        "sp",
        "Pa",
        0.5,
        (30000.0, 115000.0),
        vmin=30000.0,
        api_multiplier=100.0,
    ),
    LayerDefinition("uv_index", "uv_index", ("uv_index",), "uv_index", "index", 100.0, (0.0, 100.0)),
)

CAMS_LAYER_DEFINITIONS: tuple[LayerDefinition, ...] = (
    LayerDefinition("pm2_5", "pm2_5", ("pm2_5",), "pm2_5", "ug/m3", 10.0, (0.0, 6000.0)),
    LayerDefinition("pm10", "pm10", ("pm10",), "pm10", "ug/m3", 10.0, (0.0, 6000.0)),
    LayerDefinition("aerosol_optical_depth", "aerosol_optical_depth", ("aerosol_optical_depth",), "aod", "1", 1000.0, (0.0, 65.0)),
    LayerDefinition("dust", "dust", ("dust",), "dust", "ug/m3", 1.0, (0.0, 65535.0)),
)

# ECMWF Open Data does not expose GFS visibility or UV fields. All remaining
# surface layers keep the identical encoding contract used by the GFS product.
ECMWF_LAYER_DEFINITIONS: tuple[LayerDefinition, ...] = tuple(
    layer
    for layer in DEFAULT_LAYER_DEFINITIONS
    if layer.name not in {"vis", "uv_index"}
)


def layer_definitions_for_scope(scope: str) -> tuple[LayerDefinition, ...]:
    if scope == "gfs":
        return DEFAULT_LAYER_DEFINITIONS
    if scope == "cams":
        return CAMS_LAYER_DEFINITIONS
    if scope == "ecmwf":
        return ECMWF_LAYER_DEFINITIONS
    raise ValueError(f"unknown layer scope: {scope}")


def layer_encoding(layer: LayerDefinition) -> str:
    if layer.data_type == "vector":
        return "uv"
    if layer.data_type == "categorical":
        return "categorical"
    return "scalar"


GFS_LAYER_RESOLUTIONS: dict[str, str] = {
    "cloud_total_1": "13km",
    "cloud_high_1": "13km",
    "cloud_mid_1": "13km",
    "cloud_low_1": "13km",
    "t2m": "13km",
    "d2m": "13km",
    "r2": "13km",
    "wind": "13km",
    "tp": "13km",
    "snod": "13km",
    "gust": "28km",
    "vis": "28km",
    "precip_phase": "28km(13+28)",
    "thunderstorm_code": "28km(13+28)",
    "cape": "28km",
    "prmsl": "28km",
    "sp": "28km(13+28)",
    "uv_index": "13km",
}

CAMS_LAYER_RESOLUTIONS: dict[str, str] = {
    "pm2_5": "44km",
    "pm10": "44km",
    "aerosol_optical_depth": "44km",
    "dust": "44km",
}

ECMWF_LAYER_RESOLUTIONS: dict[str, str] = {
    layer.name: "25km" for layer in ECMWF_LAYER_DEFINITIONS
}


def layer_resolution_for_layer(scope: str, layer_name: str) -> str:
    if scope == "gfs":
        return GFS_LAYER_RESOLUTIONS[layer_name]
    if scope == "cams":
        return CAMS_LAYER_RESOLUTIONS[layer_name]
    if scope == "ecmwf":
        return ECMWF_LAYER_RESOLUTIONS[layer_name]
    raise ValueError(f"unknown layer scope: {scope}")


def layer_catalog_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "products": {
            "gfs": {
                "source": "gfs",
                "manifest": manifest_filename_for_scope("gfs"),
                "file_pattern": "{timestamp}_{batch}.webp",
                "layers": {
                    layer.name: layer.manifest(source_resolution=layer_resolution_for_layer("gfs", layer.name))
                    for layer in DEFAULT_LAYER_DEFINITIONS
                },
            },
            "cams": {
                "source": "cams",
                "manifest": manifest_filename_for_scope("cams"),
                "file_pattern": "{timestamp}_{batch}.webp",
                "layers": {
                    layer.name: layer.manifest(source_resolution=layer_resolution_for_layer("cams", layer.name))
                    for layer in CAMS_LAYER_DEFINITIONS
                },
            },
            "ecmwf": {
                "source": "ecmwf",
                "manifest": manifest_filename_for_scope("ecmwf"),
                "file_pattern": "{timestamp}_{batch}.webp",
                "layers": {
                    layer.name: layer.manifest(
                        source_resolution=layer_resolution_for_layer(
                            "ecmwf", layer.name
                        )
                    )
                    for layer in ECMWF_LAYER_DEFINITIONS
                },
            },
        },
    }


def load_layer_catalog(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def layer_api_options_for_scope(scope: str) -> dict[str, str]:
    if scope == "gfs":
        return dict(GFS_LAYER_API_OPTIONS)
    if scope == "cams":
        return dict(CAMS_LAYER_API_OPTIONS)
    if scope == "ecmwf":
        return dict(ECMWF_LAYER_API_OPTIONS)
    raise ValueError(f"unknown layer scope: {scope}")


def default_api_base_url_for_scope(scope: str) -> str:
    if scope == "gfs":
        return DEFAULT_GFS_API_BASE_URL
    if scope == "cams":
        return DEFAULT_CAMS_API_BASE_URL
    if scope == "ecmwf":
        return DEFAULT_ECMWF_API_BASE_URL
    raise ValueError(f"unknown layer scope: {scope}")


def default_output_dir_for_scope(scope: str) -> str:
    if scope == "gfs":
        return DEFAULT_GFS_OUTPUT_DIR
    if scope == "cams":
        return DEFAULT_CAMS_OUTPUT_DIR
    if scope == "ecmwf":
        return DEFAULT_ECMWF_OUTPUT_DIR
    raise ValueError(f"unknown layer scope: {scope}")


def manifest_filename_for_scope(scope: str) -> str:
    if scope == "gfs":
        return "gfs013_surface_data.json"
    if scope == "cams":
        return "cams_global_data.json"
    if scope == "ecmwf":
        return "ecmwf_ifs025_data.json"
    raise ValueError(f"unknown layer scope: {scope}")


def precip_phase_from_weather_code(weather_code: np.ndarray) -> np.ndarray:
    codes = np.asarray(weather_code, dtype=np.float32)
    out = np.zeros(codes.shape, dtype=np.float32)
    invalid = ~np.isfinite(codes)
    rounded = np.rint(np.where(invalid, 0.0, codes)).astype(np.int16)
    out[np.isin(rounded, [51, 53, 55, 61, 63, 65, 80, 81, 82])] = 1.0
    out[np.isin(rounded, [71, 73, 75, 77, 85, 86])] = 2.0
    out[np.isin(rounded, [56, 57, 66, 67])] = 4.0
    out[invalid] = np.nan
    return out


def thunderstorm_code_from_weather_code(weather_code: np.ndarray) -> np.ndarray:
    codes = np.asarray(weather_code, dtype=np.float32)
    out = np.zeros(codes.shape, dtype=np.float32)
    invalid = ~np.isfinite(codes)
    rounded = np.rint(np.where(invalid, 0.0, codes)).astype(np.int16)
    mask = np.isin(rounded, [95, 96, 99])
    out[mask] = rounded[mask].astype(np.float32)
    out[invalid] = np.nan
    return out


def derive_layer_values(layer: LayerDefinition, values: np.ndarray) -> np.ndarray:
    if layer.derive is None:
        return values
    if layer.derive == "precip_phase_from_weather_code":
        return precip_phase_from_weather_code(values)
    if layer.derive == "thunderstorm_code_from_weather_code":
        return thunderstorm_code_from_weather_code(values)
    raise ValueError(f"unknown layer derive transform: {layer.derive}")


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
    region_lat_max = lat_min + float(y1) * dy
    longitude_values = [round(region_lon_min + float(x) * dx, 6) for x in range(width)]
    latitude_values = [round(region_lat_max - float(y) * dy, 6) for y in range(height)]
    return RegionGrid(
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


def compute_ecmwf025_region_grid(
    *,
    left_lon: float,
    right_lon: float,
    bottom_lat: float,
    top_lat: float,
) -> RegionGrid:
    full_nx = 1440
    full_ny = 721
    lon_min = -180.0
    lat_min = -90.0
    dx = 0.25
    dy = 0.25
    epsilon = 1e-9
    x0 = max(0, int(math.ceil((left_lon - lon_min) / dx - epsilon)))
    x1 = min(full_nx - 1, int(math.floor((right_lon - lon_min) / dx + epsilon)))
    y0 = max(0, int(math.ceil((bottom_lat - lat_min) / dy - epsilon)))
    y1 = min(full_ny - 1, int(math.floor((top_lat - lat_min) / dy + epsilon)))
    if x0 > x1 or y0 > y1:
        raise ValueError(
            "configured region does not overlap ECMWF IFS025 source grid"
        )

    width = x1 - x0 + 1
    height = y1 - y0 + 1
    region_lon_min = lon_min + float(x0) * dx
    region_lat_min = lat_min + float(y0) * dy
    region_lat_max = lat_min + float(y1) * dy
    longitude_values = [
        round(region_lon_min + float(x) * dx, 6) for x in range(width)
    ]
    latitude_values = [
        round(region_lat_max - float(y) * dy, 6) for y in range(height)
    ]
    return RegionGrid(
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


def compute_region_grid_for_scope(
    scope: str,
    *,
    left_lon: float,
    right_lon: float,
    bottom_lat: float,
    top_lat: float,
) -> RegionGrid:
    if scope == "ecmwf":
        return compute_ecmwf025_region_grid(
            left_lon=left_lon,
            right_lon=right_lon,
            bottom_lat=bottom_lat,
            top_lat=top_lat,
        )
    if scope in {"gfs", "cams"}:
        return compute_gfs013_region_grid(
            left_lon=left_lon,
            right_lon=right_lon,
            bottom_lat=bottom_lat,
            top_lat=top_lat,
        )
    raise ValueError(f"unknown layer scope: {scope}")


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
    api_options: Mapping[str, str] | None = None,
) -> dict[str, str]:
    return build_layer_api_params(
        scope="gfs",
        latitudes=latitudes,
        longitudes=longitudes,
        variables=variables,
        model=model,
        domain=None,
        start_hour=start_hour,
        end_hour=end_hour,
        api_options=api_options,
    )


def build_layer_api_params(
    *,
    scope: str,
    latitudes: Sequence[float],
    longitudes: Sequence[float],
    variables: Sequence[str],
    model: str | None,
    domain: str | None,
    start_hour: str,
    end_hour: str,
    api_options: Mapping[str, str] | None = None,
    run: str | None = None,
    request_forecast_hours: int | None = None,
) -> dict[str, str]:
    params = {
        "latitude": ",".join(f"{value:.6f}" for value in latitudes),
        "longitude": ",".join(f"{value:.6f}" for value in longitudes),
        "hourly": ",".join(variables),
        "timezone": "UTC",
        "cell_selection": "land",
    }
    if run:
        params["run"] = run
        params["forecast_hours"] = str(request_forecast_hours or 1)
    else:
        params["start_hour"] = start_hour
        params["end_hour"] = end_hour
    if scope == "gfs":
        params["models"] = model or DEFAULT_LAYER_MODEL
    elif scope == "cams":
        params["domains"] = domain or DEFAULT_CAMS_DOMAIN
    elif scope == "ecmwf":
        params["models"] = model or DEFAULT_ECMWF_MODEL
    else:
        raise ValueError(f"unknown layer scope: {scope}")
    params.update(dict(layer_api_options_for_scope(scope) if api_options is None else api_options))
    return params


def build_export_request_payload(
    *,
    scope: str,
    grid: RegionGrid,
    variables: Sequence[str],
    model: str | None,
    domain: str | None,
    start_hour: str,
    end_hour: str,
    run: str | None = None,
    chunk_size: int = 50,
) -> dict[str, Any]:
    """Describe the immutable HTTP inputs used by the legacy export tool."""
    if scope == "gfs":
        selected_model = model or DEFAULT_LAYER_MODEL
    elif scope == "cams":
        selected_model = domain or DEFAULT_CAMS_DOMAIN
    elif scope == "ecmwf":
        selected_model = model or DEFAULT_ECMWF_MODEL
    else:
        raise ValueError(f"unknown layer scope: {scope}")
    return {
        "scope": scope,
        "model": selected_model,
        "run": run,
        "start_hour": start_hour,
        "end_hour": end_hour,
        "variables": list(variables),
        "chunk_size": chunk_size,
        "width": grid.width,
        "height": grid.height,
        "latitudes": grid.latitude_values,
        "longitudes": grid.longitude_values,
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
    api_options: Mapping[str, str] | None = None,
    timeout_seconds: float,
    request_retries: int = 0,
    request_retry_delay: float = 2.0,
    request_pause: float = 0.0,
) -> list[dict[str, Any]]:
    return fetch_layer_api_chunk(
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
        request_retries=request_retries,
        request_retry_delay=request_retry_delay,
        request_pause=request_pause,
    )


def fetch_layer_api_chunk(
    *,
    api_base_url: str,
    latitudes: Sequence[float],
    longitudes: Sequence[float],
    variables: Sequence[str],
    scope: str,
    model: str | None,
    domain: str | None,
    start_hour: str,
    end_hour: str,
    api_options: Mapping[str, str] | None = None,
    timeout_seconds: float,
    api_host_header: str | None = None,
    run: str | None = None,
    request_forecast_hours: int | None = None,
    request_retries: int = 0,
    request_retry_delay: float = 2.0,
    request_pause: float = 0.0,
) -> list[dict[str, Any]]:
    params = build_layer_api_params(
        scope=scope,
        latitudes=latitudes,
        longitudes=longitudes,
        variables=variables,
        model=model,
        domain=domain,
        start_hour=start_hour,
        end_hour=end_hour,
        api_options=api_options,
        run=run,
        request_forecast_hours=request_forecast_hours,
    )
    headers = {"Host": api_host_header} if api_host_header else None
    attempts = max(0, request_retries) + 1
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(api_base_url, params=params, headers=headers, timeout=timeout_seconds)
            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()
            response.raise_for_status()
            break
        except requests.HTTPError as error:
            status_code = error.response.status_code if error.response is not None else None
            can_retry = status_code == 429 or (status_code is not None and status_code >= 500)
            if attempt >= attempts or not can_retry:
                raise
            time.sleep(request_retry_delay)
        except requests.RequestException:
            if attempt >= attempts:
                raise
            time.sleep(request_retry_delay)
    if request_pause > 0:
        time.sleep(request_pause)
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


def frame_timestamps(times: Sequence[str]) -> list[int]:
    return [int(parse_utc_hour(value).timestamp()) for value in times]


def frame_stems(times: Sequence[str], source_start_hour: str) -> list[str]:
    batch = int(parse_utc_hour(source_start_hour).timestamp())
    return [f"{valid_ts}_{batch}" for valid_ts in frame_timestamps(times)]


def build_manifest_payload(
    *,
    scope: str,
    grid: RegionGrid,
    batch: int,
    files: Sequence[int],
    generated_at: int,
) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "source": scope,
        "batch": batch,
        "frame_count": len(files),
        "frame_step_seconds": 3600,
        "file_pattern": "{timestamp}_{batch}.webp",
        "files": list(files),
        "grid": grid.manifest(),
    }


def request_forecast_hours_for_window(*, run: str | None, end_hour: str) -> int | None:
    if not run:
        return None
    run_dt = parse_utc_hour(run)
    end_dt = parse_utc_hour(end_hour)
    if end_dt < run_dt:
        raise ValueError("--end-hour must not be before --run")
    return int((end_dt - run_dt).total_seconds() // 3600) + 1


def trim_response_to_time_window(response: list[dict[str, Any]], *, start_hour: str, end_hour: str) -> list[dict[str, Any]]:
    start_dt = parse_utc_hour(start_hour)
    end_dt = parse_utc_hour(end_hour)
    trimmed_response: list[dict[str, Any]] = []
    for item in response:
        copied = dict(item)
        hourly = dict(copied.get("hourly") or {})
        times = list(hourly.get("time") or [])
        selected = [
            index
            for index, value in enumerate(times)
            if start_dt <= parse_utc_hour(str(value)) <= end_dt
        ]
        if selected:
            for key, value in list(hourly.items()):
                if isinstance(value, list) and len(value) == len(times):
                    hourly[key] = [value[index] for index in selected]
            copied["hourly"] = hourly
        trimmed_response.append(copied)
    return trimmed_response


def selected_layers(names: str | None, *, scope: str = "gfs") -> tuple[LayerDefinition, ...]:
    definitions = layer_definitions_for_scope(scope)
    if not names:
        return definitions
    requested = {name.strip() for name in names.split(",") if name.strip()}
    layers = tuple(layer for layer in definitions if layer.name in requested)
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


def publish_build(
    build_dir: Path,
    output_dir: Path,
    filenames: set[str],
    manifest: dict[str, Any],
    *,
    manifest_filename: str,
    subdirs: Sequence[str],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for subdir in subdirs:
        (output_dir / subdir).mkdir(parents=True, exist_ok=True)
        source_dir = build_dir / subdir
        for filename in sorted(filenames):
            (source_dir / filename).replace(output_dir / subdir / filename)
        for existing in (output_dir / subdir).glob("*.webp"):
            if existing.name not in filenames:
                existing.unlink()
    manifest_path = output_dir / manifest_filename
    tmp_manifest = manifest_path.with_suffix(manifest_path.suffix + f".tmp.{os.getpid()}")
    tmp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_manifest.replace(manifest_path)


def build_layers(
    *,
    api_base_url: str,
    output_dir: Path,
    start_hour: str,
    end_hour: str,
    left_lon: float,
    right_lon: float,
    bottom_lat: float,
    top_lat: float,
    layer_names: str | None,
    chunk_size: int,
    timeout_seconds: float,
    scope: str = "gfs",
    model: str | None = DEFAULT_LAYER_MODEL,
    domain: str | None = None,
    api_host_header: str | None = None,
    run: str | None = None,
    request_retries: int = 0,
    request_retry_delay: float = 2.0,
    request_pause: float = 0.0,
) -> dict[str, Any]:
    grid = compute_region_grid_for_scope(
        scope,
        left_lon=left_lon,
        right_lon=right_lon,
        bottom_lat=bottom_lat,
        top_lat=top_lat,
    )
    layers = selected_layers(layer_names, scope=scope)
    variables = required_api_variables(layers)
    api_options = layer_api_options_for_scope(scope)
    request_forecast_hours = request_forecast_hours_for_window(run=run, end_hour=end_hour)
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
        first_response = fetch_layer_api_chunk(
            api_base_url=api_base_url,
            latitudes=first_latitudes,
            longitudes=first_longitudes,
            variables=variables,
            scope=scope,
            model=model,
            domain=domain,
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
            first_response = trim_response_to_time_window(first_response, start_hour=start_hour, end_hour=end_hour)
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
            response = fetch_layer_api_chunk(
                api_base_url=api_base_url,
                latitudes=latitudes,
                longitudes=longitudes,
                variables=variables,
                scope=scope,
                model=model,
                domain=domain,
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
                response = trim_response_to_time_window(response, start_hour=start_hour, end_hour=end_hour)
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

        file_timestamps = frame_timestamps(times)
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
                    values = derive_layer_values(layer, values)
                    values = values * np.float32(layer.api_multiplier)
                    rgba = encode_scalar_rgba(values, vmin=layer.vmin, scale=layer.scale)
                save_webp_rgba(rgba, layer_dir / f"{stem}.webp")
            print(f"[openmeteo-layers] rendered {layer.name} frames={len(stems)}", flush=True)

        manifest = build_manifest_payload(
            scope=scope,
            grid=grid,
            batch=int(parse_utc_hour(start_hour).timestamp()),
            files=file_timestamps,
            generated_at=int(time.time()),
        )
        publish_build(
            build_dir,
            output_dir,
            filenames,
            manifest,
            manifest_filename=manifest_filename_for_scope(scope),
            subdirs=[layer.subdir for layer in layers],
        )
        return manifest
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build regional WebP weather layers from the local Open-Meteo API.")
    parser.add_argument("--scope", choices=["gfs", "cams", "ecmwf"], default="gfs")
    parser.add_argument("--api-base-url", default=None)
    parser.add_argument("--api-host-header")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--domain", default=DEFAULT_CAMS_DOMAIN)
    parser.add_argument("--run", help="Pinned run for single-runs API mode, e.g. 2026-06-26T06:00.")
    parser.add_argument("--start-hour", required=True, help="UTC start hour, for example 2026-06-25T07:00")
    parser.add_argument("--end-hour", required=True, help="UTC included end hour, for example 2026-06-27T08:00")
    parser.add_argument("--left-lon", type=float, default=float(os.environ.get("WEATHER_REGION_LEFT_LON", "70.0")))
    parser.add_argument("--right-lon", type=float, default=float(os.environ.get("WEATHER_REGION_RIGHT_LON", "140.0")))
    parser.add_argument("--bottom-lat", type=float, default=float(os.environ.get("WEATHER_REGION_BOTTOM_LAT", "0.0")))
    parser.add_argument("--top-lat", type=float, default=float(os.environ.get("WEATHER_REGION_TOP_LAT", "58.0")))
    parser.add_argument("--layers", default=None, help="Comma-separated layer names. Defaults to all surface layers.")
    parser.add_argument("--chunk-size", type=int, default=int(os.environ.get("WEATHER_OPENMETEO_LAYER_CHUNK_SIZE", "250")))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.environ.get("WEATHER_OPENMETEO_LAYER_TIMEOUT", "120")))
    parser.add_argument("--request-retries", type=int, default=int(os.environ.get("WEATHER_OPENMETEO_LAYER_REQUEST_RETRIES", "2")))
    parser.add_argument("--request-retry-delay", type=float, default=float(os.environ.get("WEATHER_OPENMETEO_LAYER_REQUEST_RETRY_DELAY", "2")))
    parser.add_argument("--request-pause", type=float, default=float(os.environ.get("WEATHER_OPENMETEO_LAYER_REQUEST_PAUSE", "0")))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_base_url = args.api_base_url or default_api_base_url_for_scope(args.scope)
    output_dir = Path(args.output_dir or default_output_dir_for_scope(args.scope))
    model = args.model
    if model is None:
        model = (
            DEFAULT_ECMWF_MODEL
            if args.scope == "ecmwf"
            else DEFAULT_LAYER_MODEL
        )
    manifest = build_layers(
        api_base_url=api_base_url,
        output_dir=output_dir,
        scope=args.scope,
        model=model,
        domain=args.domain if args.scope == "cams" else None,
        start_hour=args.start_hour,
        end_hour=args.end_hour,
        left_lon=args.left_lon,
        right_lon=args.right_lon,
        bottom_lat=args.bottom_lat,
        top_lat=args.top_lat,
        layer_names=args.layers,
        chunk_size=args.chunk_size,
        timeout_seconds=args.timeout_seconds,
        api_host_header=args.api_host_header,
        run=args.run,
        request_retries=args.request_retries,
        request_retry_delay=args.request_retry_delay,
        request_pause=args.request_pause,
    )
    print(
        "[openmeteo-layers] ready "
        f"source={manifest['source']} batch={manifest['batch']} frames={manifest['frame_count']} "
        f"grid={manifest['grid']['width']}x{manifest['grid']['height']} "
        f"output={output_dir}",
        flush=True,
    )


if __name__ == "__main__":
    main()
