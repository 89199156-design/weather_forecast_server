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

The vendored `vendor/open-meteo` tree is kept as the upstream source at the
recorded baseline commit. Do not patch Open-Meteo reader behavior,
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

None. `vendor/open-meteo` is intended to match the baseline commit byte-for-byte
except for line-ending normalization performed by Git on checkout.
