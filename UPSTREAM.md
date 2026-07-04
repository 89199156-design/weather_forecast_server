# Upstream Source Record

This project directly vendors and runs Open-Meteo source code. Keep this file
updated whenever upstream code is imported, rebased, or patched.

## Open-Meteo Engine

- Repository: `https://github.com/open-meteo/open-meteo`
- License: GNU Affero General Public License v3.0 or later
- Baseline commit selected for current GFS and CAMS parity:
  `4efb9c49fb4a3718ed385fb22580d2e0fc56bdb2`
- Commit subject:
  `fix: weather codes slightly less thunderstorm lat scaling`
- Reason for this baseline:
  This single upstream commit contains the GFS weather-code behavior and
  JSON/CSV numeric writer behavior validated against the current public
  Open-Meteo APIs. The vendored tree must not mix reader, weather-code, writer,
  interpolation, or model-fallback files from different Open-Meteo commits.

## Open-Meteo SDK

- Repository: `https://github.com/open-meteo/sdk`
- Baseline commit selected by Open-Meteo `Package.resolved`:
  `a29c4b62dd8445128e6db30f0a6fb5509fa1259c`
- Commit subject: `fix: use java version 17 for build (#262)`

## Previous Internal Source

- Repository: `89199156-design/weather_server_gfs`
- Migration reference commit:
  `fe6d0e246d43c134221cef1a5554bc73162d47b8`
- Usage rule:
  read-only reference or source for non-satellite export/deployment tooling.
  The old remote repository must not be modified by this migration.

## Local Modification Boundary

The vendored `vendor/open-meteo` tree is kept as close as possible to the
recorded upstream baseline. Do not patch Open-Meteo reader behavior,
interpolation, model fallback, weather-code derivation, API serialization, or
unit precision in the vendored tree.

Allowed vendored changes are limited to product-source integration that cannot
stay outside the engine:

- configured China/surrounding-region domain grids;
- configured source-data download endpoints and area requests;
- configured output variable selection.
- project-specific China AQI derived variables;
- direct layer-grid export command that calls the Open-Meteo reader path without
  running the local HTTP API.

Local changes must stay outside the vendored engine:

- external mirror/sync configuration;
- generated client layer WebP encoding/manifest logic;
- validation tools;
- deployment scripts and task definitions.

Weather-code derivation, model reader behavior, interpolation, model fallback,
unit precision, and JSON/API semantics must come from recorded Open-Meteo source
paths, not from a separately maintained Python clone or local formula.

## Local Patches In Vendored Open-Meteo

Current intentional differences from upstream `4efb9c49`:

- `Dockerfile`: copies Arrow/Parquet runtime libraries needed by the packaged
  image.
- `CamsDomain.swift`: `cams_global` uses the configured China/surrounding-region
  grid slice and exposes a helper used by the ADS/CDS area downloader.
- `CamsDownload.swift`: `cams_global` uses ECMWF CAMS FTP/ECPDS credentials.
  FTP/ECPDS NetCDF fields are cropped to the configured China/surrounding-region
  grid slice before Open-Meteo writes `.om` files. Multi-level CAMS files are
  requested hourly rather than filtered to 3-hour steps.
- `CamsDownloadAds.swift`: the project-authorized ADS/CDS area request is kept
  as a separate backup command. It is not selected by the FTP/ECPDS production
  path and has no shared source-switch branch.
- `CamsGreenhouseGases.swift`: greenhouse-gas ADS helper is attached to the
  ADS/CDS command after the command split.
- `CamsReader.swift`, `VariableHourly.swift`, `AirQuality.swift`, and
  `FlatBuffers+WeatherApi.swift`: add project-specific `ch_aqi` and
  `ch_iaqi_*` variables. These are not official Open-Meteo parity variables.
- `configure.swift`: registers the isolated `download-cams-ads` command and the
  direct `export-layer-grid` command.
- `LayerGridExportCommand.swift`: exports grid values by calling the same
  Open-Meteo reader/mixing path used after API request parsing, avoiding the
  removed internal HTTP hop.
- GFS domain/download files also contain the configured China/surrounding-region
  source and production area adaptations.

Any other vendored difference requires a root-cause note and validation record
before it can be treated as intentional.
