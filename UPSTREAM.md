# Upstream Source Record

This project directly vendors and runs Open-Meteo source code. Keep this file
updated whenever upstream code is imported, rebased, or patched.

## Open-Meteo Forecast/GFS Engine

- Repository: `https://github.com/open-meteo/open-meteo`
- License: GNU Affero General Public License v3.0 or later
- Baseline commit selected for current `single-runs` / GFS parity:
  `036c1d940f2dd5af48f899c2d8162d00d12d3c49`
- Commit subject:
  `feat: option to generate data_run for IFS after only a certain amount of forecast hours (#1886)`
- Reason for this baseline:
  It is the upstream Open-Meteo commit immediately before
  `98a3e0f00bf13633c5511a6c7788462088bfe752`, which changed JSON/CSV float
  formatting. The current public `single-runs-api.open-meteo.com` API still
  serializes GFS `temperature_2m` like the pre-`98a3e0f0` writer, while the
  flatbuffers raw values match.
- GFS weather-code API behavior baseline:
  `4efb9c49fb4a3718ed385fb22580d2e0fc56bdb2`
- Weather-code commit subject:
  `fix: weather codes slightly less thunderstorm lat scaling`
- Weather-code reason:
  The public `single-runs-api.open-meteo.com` weather-code output for pinned
  GFS runs matches this upstream weather-code path. Earlier `2ccf980f` over-
  triggers tropical thunderstorms, while later `3a64572c` blocks low-cloud-cover
  thunderstorm cases that the current public API still returns as `95`.

## Open-Meteo Air-Quality/CAMS Engine

- Repository: `https://github.com/open-meteo/open-meteo`
- License: GNU Affero General Public License v3.0 or later
- Active local shared engine baseline:
  `036c1d940f2dd5af48f899c2d8162d00d12d3c49`
- Commit subject:
  `feat: option to generate data_run for IFS after only a certain amount of forecast hours (#1886)`
- Current status:
  The public `air-quality-api.open-meteo.com` response headers checked on
  2026-06-30 did not expose a build commit. Current local file-level audit shows
  `Package.swift`, API writers, and `GenericVariableHandle.swift` match upstream
  `036c1d94`. CAMS source differences are limited to the configured regional
  grid and the project-authorized FTP/ECPDS and ADS/CDS download commands.
- Historical candidate:
  `acfb7eb13ffdca9d3772c57716c240d3a7d73da5` was previously recorded as an
  air-quality writer candidate. Treat it as historical evidence only until a
  fresh file-level build/runtime audit proves it is the active source version.

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

Current intentional differences from upstream `036c1d94`:

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
- `WeatherCode.swift`, GFS reader/controller weather-code wiring, and shared
  weather-code call sites match the upstream `4efb9c49` weather-code behavior
  used by the current public `single-runs-api.open-meteo.com` API.

Any other vendored difference requires a root-cause note and validation record
before it can be treated as intentional.
