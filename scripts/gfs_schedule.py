#!/usr/bin/env python3
"""Shared official GFS forecast-step schedule."""

from __future__ import annotations


def gfs_forecast_hours(max_forecast_hour: int) -> list[int]:
    """Return GFS steps: hourly through 120h, then 3-hourly through 384h."""
    if max_forecast_hour < 0:
        raise ValueError("max_forecast_hour must not be negative")
    if max_forecast_hour > 384:
        raise ValueError("max_forecast_hour must not exceed the official 384h horizon")

    hourly_end = min(max_forecast_hour, 120)
    hours = list(range(hourly_end + 1))
    if max_forecast_hour > 120:
        hours.extend(range(123, max_forecast_hour + 1, 3))
    return hours


def gfs_full_run_hours(max_forecast_hour: int) -> list[int]:
    """Return the dense public hourly axis stored in native per-run files."""
    # Reuse the source-schedule validation so both representations enforce the
    # same official horizon.
    gfs_forecast_hours(max_forecast_hour)
    return list(range(max_forecast_hour + 1))
