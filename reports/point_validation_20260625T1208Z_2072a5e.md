# Point API validation - 2072a5e

Date: 2026-06-25
Candidate image: `weather-forecast-openmeteo:2072a5e`
Upstream engine: vendored Open-Meteo source
Run validated against public Open-Meteo API: `gfs013` 2026-06-25 06Z
Candidate data window: `2026-06-25T06:00` to `2026-06-27T08:00`
Validation frame window: `2026-06-25T07:00` to `2026-06-27T08:00`

## Configuration

- API endpoint under test: `http://127.0.0.1:18080/v1/forecast`
- Official reference: `https://api.open-meteo.com/v1/forecast`
- Model: `gfs013`
- Region: lon `70..140`, lat `0..58`
- DEM source for parity validation: `WEATHER_DEM_REMOTE_DATA_DIRECTORY=https://openmeteo.s3.amazonaws.com/data/`
- Forecast remote archive: not enabled

## Variables

- `temperature_2m`
- `relative_humidity_2m`
- `precipitation`
- `cloud_cover`
- `wind_speed_10m`
- `wind_direction_10m`
- `wind_u_component_10m`
- `wind_v_component_10m`
- `shortwave_radiation`

## Gates

| Gate | Points | Frames per point | Values checked | Result |
| --- | ---: | ---: | ---: | --- |
| 1 | 50 | 50 | 22,500 | pass, 0 mismatches |
| 2 | 100 | 50 | 45,000 | pass, 0 mismatches |
| 3 | 500 | 50 | 225,000 | pass, 0 mismatches |

## Notes

- A previous apparent mismatch was caused by public Open-Meteo switching from 00Z to 06Z during validation. The final gates were run after the candidate was regenerated with the same 06Z run as the public API.
- `2072a5e` fixes regional NOAA filter GRIB repacking noise by rounding decoded regional values to the GRIB message `decimalScaleFactor` before Open-Meteo `.om` compression.
- The default API path was tested with DEM enabled. For commercial production, mirror the required `copernicus_dem90` static files to an owned data source and set `WEATHER_DEM_REMOTE_DATA_DIRECTORY` to that mirror.
