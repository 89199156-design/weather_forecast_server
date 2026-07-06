# Open-Meteo Baseline Audit 2026-07-06

Baseline recorded in `UPSTREAM.md`: `open-meteo/open-meteo@4efb9c49fb4a3718ed385fb22580d2e0fc56bdb2`.

## Core Logic Check

Text-normalized comparison against upstream `4efb9c49` matched for the core files that must not carry local weather/API logic:

- `Sources/App/Helper/WeatherCode.swift`
- `Sources/App/Gfs/GfsController.swift`
- `Sources/App/Controllers/VariableHourly.swift`
- `Sources/App/Helper/Reader/DerivedMapping.swift`
- `Sources/App/Helper/Writer/JsonWriter.swift`
- `Sources/App/Helper/NumberExtensions.swift`

The first byte-hash check differed for several files because local checkout line endings differ. A unified text diff showed zero diff lines for the files above.

## Vendored Difference Inventory

After normalizing line endings, the vendored tree differs from upstream in these runtime-relevant files:

- `Sources/App/Cams/CamsDomain.swift`
- `Sources/App/Cams/CamsDownload.swift`
- `Sources/App/Cams/CamsDownloadAds.swift`
- `Sources/App/Cams/CamsGreenhouseGases.swift`
- `Sources/App/Cams/CamsReader.swift`
- `Sources/App/Cams/CamsRegionalDownload.swift`
- `Sources/App/Gfs/GfsDomain.swift`
- `Sources/App/Gfs/GfsDownload.swift`
- `Sources/App/Helper/AirQuality.swift`
- `Sources/App/configure.swift`
- `Sources/App/Commands/LayerGridExportCommand.swift`
- `Sources/App/Commands/PointForecastExportCommand.swift`

These correspond to China/surrounding-region source adaptation, CAMS FTP/ECPDS and ADS/CDS separation, project China AQI variables, and non-HTTP export commands.

Non-runtime or documentation/test differences also exist:

- `.github/workflows/docker.yml`
- `Dockerfile`
- `README.md`
- `Public/docs/openapi.yml`
- `openapi.yml`
- `openapi_historical_weather_api.yml`
- selected upstream test files

## Current External Validation Availability

Current Singapore production data at audit time:

- GFS `.om`: `ncep_gfs013=2026-07-06T06:00:00Z`, `ncep_gfs025=2026-07-06T06:00:00Z`
- CAMS `.om`: `cams_global=2026-07-06T00:00:00Z`
- GFS WebP manifest: batch `1783317600`, `121` frames
- CAMS WebP manifest: batch `1783296000`, `121` frames

Official source probes:

- GFS `2026-07-06T12Z`: not ready, NOAA index `gfs.t12z.pgrb2.0p25.f000.idx` returned `404`.
- CAMS FTP/ECPDS `2026-07-06T12Z`: not ready, `CAMS_GLOBAL_ADDITIONAL` `so2` model-level file returned `404`.

Official API availability probe:

- `single-runs-api.open-meteo.com` `run=2026-07-06T06:00` returned `400`: `ncep_gfs025` run not available.
- `single-runs-api.open-meteo.com` `run=2026-07-06T00:00` returned `200`.

Therefore the current local GFS `06Z` batch cannot yet be strict-validated against the official single-runs API. This is an external reference-run availability issue, not a recorded value mismatch.
