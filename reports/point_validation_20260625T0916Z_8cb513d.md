# Point API Validation Gate Report

- Version under test: `8cb513d`
- Gate: preflight single-point comparison before the required 50 points x 50 frames batch
- Local endpoint: `http://127.0.0.1:18080/v1/forecast`
- Reference endpoint: `https://api.open-meteo.com/v1/forecast`
- Query shape: `models=gfs013`, `forecast_hours=50`, `timezone=UTC`

## Result

Failed before starting the 50-point gate.

The local API and Open-Meteo API returned the same model grid coordinate near Shanghai, but local values were not aligned:

- Reference elevation: `3.0`
- Local elevation: `277.0`
- Reference `temperature_2m` at `2026-06-25T09:00`: `25.4`
- Local `temperature_2m` at `2026-06-25T09:00`: `35.4`

## Root Cause

`gfs013` remains a global Open-Meteo domain. The regional NOAA-filtered download reuses the same domain, but the post-load grid transform checked `domain.isGlobal` before checking whether the data came from a regional filtered grid.

That caused cropped `70E..140E` data to receive Open-Meteo's full-global `shift180LongitudeAndFlipLatitude()` transform. The transform is correct for original full global GRIB, but corrupts longitude ordering for regional filtered GRIB.

## Fix

For both elevation and forecast fields, apply regional filtered handling first:

- Regional filtered grid: `flipLatitude()`
- Non-filtered global grid: `shift180LongitudeAndFlipLatitude()`

The Open-Meteo weather parsing and variable logic remain unchanged.
