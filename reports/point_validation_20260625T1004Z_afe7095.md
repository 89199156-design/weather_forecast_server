# Point API Validation Gate Report

- Version under test: `afe7095`
- Gate: preflight single-point comparison before the required 50 points x 50 frames batch
- Local endpoint: `http://127.0.0.1:18080/v1/forecast`
- Reference endpoint: `https://api.open-meteo.com/v1/forecast`
- Query shape: `models=gfs013`, `start_hour=2026-06-25T06:00`, `end_hour=2026-06-27T07:00`, `timezone=UTC`

## Result

Failed before starting the 50-point gate.

The local API and Open-Meteo API returned the same model grid coordinate near Shanghai, but local values still did not match:

- Reference elevation: `3.0`
- Local elevation: `0.0`
- Reference `temperature_2m` at `2026-06-25T06:00`: `26.6`
- Local `temperature_2m` at `2026-06-25T06:00`: `27.0`

## Root Cause

The previous fix correctly stopped applying the full-global longitude shift to regional NOAA-filtered GRIB. However, it still applied `flipLatitude()` to regional filtered GRIB.

Direct GRIB metadata inspection showed the NOAA subregion output is already aligned with the regional grid:

- `latitudeOfFirstGridPointInDegrees=0.058574`
- `longitudeOfFirstGridPointInDegrees=70.0781`
- `latitudeOfLastGridPointInDegrees=57.9304`
- `longitudeOfLastGridPointInDegrees=139.922`
- `iScansNegatively=0`
- `jScansPositively=1`
- `scanningMode=64`

Therefore, regional NOAA-filtered GRIB should not be flipped.

## Fix

For regional filtered grids, apply no post-load transform. Keep Open-Meteo's original `shift180LongitudeAndFlipLatitude()` only for non-filtered global GRIB.
