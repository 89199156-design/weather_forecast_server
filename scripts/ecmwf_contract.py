#!/usr/bin/env python3
"""Pinned Singapore ECMWF IFS 0.25° production contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

OPENMETEO_UPSTREAM_COMMIT = "b743cbc9a7fab3f8f7dda85968fb770eee48b9ec"
MODEL = "ecmwf_ifs025"
DOMAIN = "ifs025"
PUBLIC_BOUNDS = (70.0, 140.0, 0.0, 58.0)
STORAGE_BOUNDS = (68.0, 142.0, -2.0, 60.0)
PRESSURE_LEVELS_HPA = (
    1000,
    925,
    850,
    700,
    600,
    500,
    400,
    300,
    250,
    200,
    150,
    100,
    50,
    10,
)
PRESSURE_RAW_TYPES = (
    "temperature",
    "relative_humidity",
    "geopotential_height",
    "wind_u_component",
    "wind_v_component",
    "vertical_velocity",
)
SURFACE_RAW_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "wind_u_component_10m",
    "wind_v_component_10m",
    "precipitation_type",
    "cloud_cover",
    "cloud_cover_high",
    "cloud_cover_mid",
    "cloud_cover_low",
    "pressure_msl",
    "cape",
    "snow_depth",
    "wind_gusts_10m",
    "wind_u_component_100m",
    "wind_v_component_100m",
    "surface_temperature",
    "temperature_2m_min",
    "temperature_2m_max",
    "total_column_integrated_water_vapour",
    "runoff",
    "snow_depth_water_equivalent",
    "soil_temperature_0_to_7cm",
    "soil_temperature_7_to_28cm",
    "soil_temperature_28_to_100cm",
    "soil_temperature_100_to_255cm",
    "soil_moisture_0_to_7cm",
    "soil_moisture_7_to_28cm",
    "soil_moisture_28_to_100cm",
    "soil_moisture_100_to_255cm",
    "precipitation",
    "shortwave_radiation",
    "snowfall_water_equivalent",
)
PRESSURE_RAW_VARIABLES = tuple(
    f"{kind}_{level}hPa"
    for level in PRESSURE_LEVELS_HPA
    for kind in PRESSURE_RAW_TYPES
)
RAW_VARIABLES = (*SURFACE_RAW_VARIABLES, *PRESSURE_RAW_VARIABLES)

# Open-Meteo deliberately ignores the current run's zero-valued/missing hour-0
# messages for these accumulated/extreme fields. Seed prior cycles so the same
# rolling time-series database supplies its normal predecessor values.
ROLLING_FALLBACK_VARIABLES = (
    "wind_gusts_10m",
    "temperature_2m_max",
    "temperature_2m_min",
    "shortwave_radiation",
    "precipitation",
    "runoff",
)

SURFACE_PROBE_PARAMS = {
    "2t",
    "2d",
    "10u",
    "10v",
    "ptype",
    "tcc",
    "msl",
    "mucape",
    "sd",
    "10fg",
    "100u",
    "100v",
    "skt",
    "mx2t6",
    "mn2t6",
    "tcwv",
    "ro",
    "rsn",
    "sot",
    "vsw",
    "tp",
    "ssrd",
    "sf",
}
SOIL_PROBE_FIELDS = {
    (parameter, level)
    for parameter in ("sot", "vsw")
    for level in (1, 2, 3, 4)
}
PRESSURE_PROBE_PARAMS = {"t", "r", "gh", "u", "v", "w"}


def parse_run(run: str) -> datetime:
    value = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    if value.strftime("%Y%m%d%H") != run or value.hour not in (0, 6, 12, 18):
        raise ValueError(f"invalid ECMWF run: {run}")
    return value


def natural_max_forecast_hour(run: datetime) -> int:
    return 360 if run.hour in (0, 12) else 144


def source_run_plan(target_run: str, lookback_hours: int = 72) -> tuple[tuple[str, int], ...]:
    target = parse_run(target_run)
    if target.hour != 0:
        raise ValueError("strict ECMWF comparison target must be a 00Z run")
    if lookback_hours < 6 or lookback_hours % 6:
        raise ValueError("lookback_hours must be a positive multiple of six")
    runs = []
    cursor = target - timedelta(hours=lookback_hours)
    while cursor < target:
        runs.append((cursor.strftime("%Y%m%d%H"), natural_max_forecast_hour(cursor)))
        cursor += timedelta(hours=6)
    runs.append((target_run, 360))
    return tuple(runs)


assert len(SURFACE_RAW_VARIABLES) == 32
assert len(PRESSURE_RAW_VARIABLES) == 84
assert len(RAW_VARIABLES) == 116
assert len(set(RAW_VARIABLES)) == len(RAW_VARIABLES)
