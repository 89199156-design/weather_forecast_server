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
  Validation against the current public Open-Meteo API showed that the older
  `acfb7eb13ffdca9d3772c57716c240d3a7d73da5` baseline missed thunderstorm
  weather-code cases, `10e3cc3902d9193a5af01650876bbc1f09ebb114` over-produced
  thunderstorm weather codes in low-latitude high-CAPE/no-precipitation cases,
  and the newer `ceb2c7244bc3cae269adb4014a4fd6909cdee1c7` baseline applied
  later thunderstorm-suppression changes that also mismatched the public API on
  the same data inputs. The vendored tree must not mix reader, weather-code,
  writer, interpolation, or model-fallback files from different Open-Meteo
  commits.

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

There are no intentional local patches inside `vendor/open-meteo`.

The vendored directory is imported as a whole from upstream
`4efb9c49fb4a3718ed385fb22580d2e0fc56bdb2`. Production code that adapts data
source credentials, China/surrounding-region handling, WebP generation,
mirroring, scheduling, or project-specific API output must live outside the
vendored Open-Meteo source tree.

Any future change under `vendor/open-meteo` must be a full upstream baseline
replacement, not a hand-written patch or a mix of files from different upstream
commits.
