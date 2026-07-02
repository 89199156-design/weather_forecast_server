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
  flatbuffers raw values match. This baseline also predates the later
  thunderstorm weather-code parameterisation commits that did not match the
  current public API during validation.

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
  grid and project-authorized ADS/CDS area download path.
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

Local changes must stay outside the vendored engine:

- external mirror/sync configuration;
- generated point-package and layer export;
- validation tools;
- deployment scripts and task definitions.

Weather-code derivation, model reader behavior, interpolation, model fallback,
unit precision, and JSON/API semantics must come from Open-Meteo source paths,
not from a separately maintained Python clone.

## Local Patches In Vendored Open-Meteo

Current intentional differences from upstream `036c1d94`:

- `CamsDomain.swift`: `cams_global` uses the configured China/surrounding-region
  grid slice.
- `CamsDownload.swift`: `cams_global` prefers ECMWF CAMS FTP/ECPDS credentials
  and keeps the project-authorized ADS/CDS area request as an explicit
  `WEATHER_CAMS_SOURCE=ads` backup path. FTP/ECPDS NetCDF fields are cropped to
  the same configured China/surrounding-region grid slice before Open-Meteo
  writes `.om` files.
- GFS domain/download files also contain the configured China/surrounding-region
  source and production area adaptations.

Any other vendored difference requires a root-cause note and validation record
before it can be treated as intentional.
