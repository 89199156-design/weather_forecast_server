#!/usr/bin/env python3
"""Grid contracts for the region-cropped native Open-Meteo domains."""

from __future__ import annotations

import math
from typing import Any


def regular_grid_slice(
    *,
    full_nx: int,
    full_ny: int,
    lat_min: float,
    lon_min: float,
    dx: float,
    dy: float,
    left_lon: float,
    right_lon: float,
    bottom_lat: float,
    top_lat: float,
    halo_cells: int,
) -> dict[str, Any]:
    if not left_lon < right_lon or not bottom_lat < top_lat:
        raise ValueError("regional bounds must be ordered and non-empty")
    epsilon = 1e-9
    x0 = max(0, math.ceil((left_lon - lon_min) / dx - epsilon) - halo_cells)
    x1 = min(full_nx - 1, math.floor((right_lon - lon_min) / dx + epsilon) + halo_cells)
    y0 = max(0, math.ceil((bottom_lat - lat_min) / dy - epsilon) - halo_cells)
    y1 = min(full_ny - 1, math.floor((top_lat - lat_min) / dy + epsilon) + halo_cells)
    if x0 > x1 or y0 > y1:
        raise ValueError("regional bounds do not overlap source grid")
    return {
        "grid_type": "regional_regular_lat_lon",
        "full_nx": full_nx,
        "full_ny": full_ny,
        "x0": x0,
        "y0": y0,
        "nx": x1 - x0 + 1,
        "ny": y1 - y0 + 1,
        "lon_min": lon_min + x0 * dx,
        "lat_min": lat_min + y0 * dy,
        "dx": dx,
        "dy": dy,
        "halo_cells": halo_cells,
        "requested_bounds": {
            "left_lon": left_lon,
            "right_lon": right_lon,
            "bottom_lat": bottom_lat,
            "top_lat": top_lat,
        },
    }


def gfs_domain_grids(
    left_lon: float = 70.0,
    right_lon: float = 140.0,
    bottom_lat: float = 0.0,
    top_lat: float = 58.0,
) -> dict[str, dict[str, Any]]:
    gfs013_dy = 0.11714935
    grids = {
        "ncep_gfs013": regular_grid_slice(
            full_nx=3072,
            full_ny=1536,
            lat_min=-gfs013_dy * (1536 - 1) / 2,
            lon_min=-180.0,
            dx=360.0 / 3072,
            dy=gfs013_dy,
            left_lon=left_lon,
            right_lon=right_lon,
            bottom_lat=bottom_lat,
            top_lat=top_lat,
            # The configured bounds already are the storage halo around the
            # public 70..140E / 0..58N region. The Swift regional download
            # crops exactly to these bounds, so adding another grid-cell halo
            # here would make the published metadata larger than the OM files.
            halo_cells=0,
        ),
        "ncep_gfs025": regular_grid_slice(
            full_nx=1440,
            full_ny=721,
            lat_min=-90.0,
            lon_min=-180.0,
            dx=0.25,
            dy=0.25,
            left_lon=left_lon,
            right_lon=right_lon,
            bottom_lat=bottom_lat,
            top_lat=top_lat,
            halo_cells=0,
        ),
    }
    for grid in grids.values():
        grid["dt_seconds"] = 3600
        grid["om_file_length"] = 481
    return grids


def cams_domain_grids(
    left_lon: float = 70.0,
    right_lon: float = 140.0,
    bottom_lat: float = 0.0,
    top_lat: float = 58.0,
) -> dict[str, dict[str, Any]]:
    grids = {
        "cams_global": regular_grid_slice(
            full_nx=900,
            full_ny=451,
            lat_min=-90.0,
            lon_min=-180.0,
            dx=0.4,
            dy=0.4,
            left_lon=left_lon,
            right_lon=right_lon,
            bottom_lat=bottom_lat,
            top_lat=top_lat,
            halo_cells=0,
        ),
        "cams_global_greenhouse_gases": regular_grid_slice(
            full_nx=3600,
            full_ny=1801,
            lat_min=-90.0,
            lon_min=-180.0,
            dx=0.1,
            dy=0.1,
            left_lon=left_lon,
            right_lon=right_lon,
            bottom_lat=bottom_lat,
            top_lat=top_lat,
            halo_cells=0,
        ),
    }
    grids["cams_global"]["dt_seconds"] = 3600
    grids["cams_global"]["om_file_length"] = 217
    grids["cams_global_greenhouse_gases"]["dt_seconds"] = 3 * 3600
    grids["cams_global_greenhouse_gases"]["om_file_length"] = 72
    return grids
