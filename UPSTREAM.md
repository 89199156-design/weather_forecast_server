# Upstream Source Record

This project directly uses and modifies Open-Meteo source code. Keep this file
updated whenever upstream code is imported, rebased, or patched.

## Open-Meteo Engine

- Repository: `https://github.com/open-meteo/open-meteo`
- License: GNU Affero General Public License v3.0 or later
- Baseline commit selected for migration:
  `d91c52f00665bb8ddd348f688fece556c933ffbb`
- Commit subject observed on Singapore:
  `fix: missing pressure level in flatbuffers encoding (#1926)`
- Reason for this baseline:
  It matches the Open-Meteo source tree used during the previous GFS parity
  audit on the Singapore server and is the latest source baseline found there
  at migration start.

## Open-Meteo SDK

- Repository: `https://github.com/open-meteo/sdk`
- Baseline commit observed through Open-Meteo `Package.resolved` and Singapore
  source checkout: `e6274cf9e10240b98219b16cf63cf0fae73347d9`
- Commit subject observed on Singapore: `fix: bump release`

## Previous Internal Source

- Repository: `89199156-design/weather_server_gfs`
- Migration reference commit:
  `fe6d0e246d43c134221cef1a5554bc73162d47b8`
- Usage rule:
  read-only reference or source for non-satellite export/deployment tooling.
  The old remote repository must not be modified by this migration.

## Local Modification Boundary

Local changes should stay in thin integration layers where possible:

- regional domain and GFS download-source configuration;
- generated point-package and layer export;
- validation tools;
- deployment scripts and task definitions.

Weather-code derivation, model reader behavior, interpolation, model fallback,
unit precision, and JSON/API semantics must come from Open-Meteo source paths,
not from a separately maintained Python clone.

## Local Patches Started In This Repository

### `vendor/open-meteo/Package.swift`

- Replaced the remote `https://github.com/open-meteo/sdk.git` SwiftPM
  dependency with the local path dependency `../openmeteo-sdk`.
- Updated the `OpenMeteoSdk` product package identity from `sdk` to
  `openmeteo-sdk`.
- Reason: keep the Open-Meteo engine and SDK source inside this public project
  so the deployed commercial network service has an auditable corresponding
  source tree.

### `vendor/open-meteo/Sources/App/Gfs/GfsDomain.swift`

- Added `WeatherForecastServerSourceConfig.baseUrl(...)`.
- Added environment-variable overrides for GFS/GEFS/HRRR/NAM base URLs:
  - `WEATHER_GFS_NOMADS_BASE_URL`
  - `WEATHER_GFS_AWS_BASE_URL`
  - `WEATHER_GEFS_NOMADS_BASE_URL`
  - `WEATHER_GEFS_AWS_BASE_URL`
  - `WEATHER_HRRR_NOMADS_BASE_URL`
  - `WEATHER_HRRR_AWS_BASE_URL`
  - `WEATHER_NAM_NOMADS_BASE_URL`
- Added environment-variable overrides for regional NOAA GFS filter endpoints:
  - `WEATHER_GFS_FILTER_0P25_URL`
  - `WEATHER_GFS_FILTER_0P25B_URL`
  - `WEATHER_GFS_FILTER_SFLUX_URL`
- Reason: allow Singapore to use our own lightweight mirror or regional
  pre-sliced download source without changing Open-Meteo reader, interpolation,
  weather-code, or API semantics.

### `vendor/open-meteo/Sources/App/Gfs/GfsDownload.swift`

- GFS downloads use Open-Meteo's existing HTTP/1.1 client and add stable NOAA
  request headers:
  - `User-Agent: curl/8.5.0`
  - `Connection: close`
- GFS025 regional filter downloads route `pgrb2b` files to NOAA's secondary
  `filter_gfs_0p25b.pl` endpoint instead of the primary `filter_gfs_0p25.pl`.
- Reason: Singapore runtime tests showed NOAA/Akamai can return repeated
  HTTP 302 responses to headerless GFS `.idx` requests from the container.
  The `pgrb2b` secondary pressure-level file also requires NOAA's secondary
  filter endpoint for regional cropping. This patch is limited to the raw-data
  download transport path and does not change Open-Meteo readers,
  interpolation, weather-code, unit conversion, or API serialization.

### `vendor/open-meteo/Sources/App/Helper/Download/Curl.swift`

- Drains up to 1 MiB from non-success HTTP responses before retrying.
- Reason: keep retry connections clean when NOAA/Akamai returns small 3xx/4xx
  bodies during transient download failures.
