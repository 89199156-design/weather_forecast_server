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
- Official GFS importer backport:
  `6059e2bd7e009b765caadd6a619002af3fd9ee21`
- Backport subject:
  `fix: GFS precipitation deaccumulation past 1-hourly data`
- Reason for this baseline:
  Validation against the current public Open-Meteo API showed that the older
  `acfb7eb13ffdca9d3772c57716c240d3a7d73da5` baseline missed thunderstorm
  weather-code cases, `10e3cc3902d9193a5af01650876bbc1f09ebb114` over-produced
  thunderstorm weather codes in low-latitude high-CAPE/no-precipitation cases,
  and the newer `ceb2c7244bc3cae269adb4014a4fd6909cdee1c7` baseline applied
  later thunderstorm-suppression changes that also mismatched the public API on
  the same data inputs. The vendored tree must not mix reader, weather-code,
  writer, interpolation, or model-fallback files from different Open-Meteo
  commits. The later official GFS importer fix above is backported unchanged so
  precipitation and showers use the real one- or three-hour forecast interval.

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

The regional native runtime also consumes per-run files written by
`GenericVariableHandle.generateFullRunData`. For `cams_global` only, this
product-source boundary expands the official three-hour model-level source
cadence to the domain's hourly axis and invokes the existing upstream
`variable.interpolation`. It does not define or modify an interpolation
formula; it mirrors the ordinary Open-Meteo time-series conversion for the
additional native per-run representation.

## Local Patches In Vendored Open-Meteo

The vendored directory starts from upstream
`4efb9c49fb4a3718ed385fb22580d2e0fc56bdb2`. The GFS precipitation interval
change is copied verbatim from upstream commit
`6059e2bd7e009b765caadd6a619002af3fd9ee21`; it is not a locally invented
formula. Product-source changes for the regional NOMADS path, regional grids,
CAMS hourly surface files, three-hour model-level files, and selected outputs
are the project-specific boundary. The
regional NOMADS adapter also restores the original source-message packing
metadata for pressure fields whose decoded values would otherwise change when
NOMADS repacks a cropped response. This input-fidelity repair does not alter
reader interpolation or forecast formulas.

Every future change under `vendor/open-meteo` must be either a recorded
upstream commit or one of the explicitly allowed product-source boundaries
above. Reader and API semantics must never be silently mixed or compensated in
the Rust adapter.
