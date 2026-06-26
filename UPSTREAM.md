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
- Baseline commit selected for current `air-quality` / CAMS parity:
  `acfb7eb13ffdca9d3772c57716c240d3a7d73da5`
- Commit subject:
  `fix wind direction for ecmwf seascape`
- Reason for this baseline:
  The current public `air-quality-api.open-meteo.com` JSON writer matches the
  post-`98a3e0f0` integer rounding behavior for derived AQI values such as
  `us_aqi`. Validation showed identical raw CAMS flatbuffer values at failing
  points, while `036c1d94` wrote `42` and the public API wrote `43` for a raw
  `42.5` AQI value. Running this unmodified upstream version for CAMS matches
  the public air-quality API without patching Open-Meteo source code.

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

The vendored `vendor/open-meteo` tree is kept as upstream source at the
recorded baseline commit for the image being built. Public API parity currently
uses separate unmodified upstream images for forecast/GFS and air-quality/CAMS
because the public Open-Meteo subdomains match different upstream writer
behaviors. Do not patch Open-Meteo reader behavior,
interpolation, model fallback, weather-code derivation, domain grids, download
transport, or API serialization in the vendored tree.

Local changes must stay outside the vendored engine:

- external mirror/sync configuration;
- generated point-package and layer export;
- validation tools;
- deployment scripts and task definitions.

Weather-code derivation, model reader behavior, interpolation, model fallback,
unit precision, and JSON/API semantics must come from Open-Meteo source paths,
not from a separately maintained Python clone.

## Local Patches In Vendored Open-Meteo

None. `vendor/open-meteo` is intended to match the selected baseline commit
byte-for-byte except for line-ending normalization performed by Git on
checkout.
