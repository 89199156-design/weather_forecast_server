# Vendored Upstream Locks

The vendored upstream directories are plain source copies, not git submodules.
Do not update them without updating this file and `../UPSTREAM.md`.

## `vendor/open-meteo`

- Upstream: `https://github.com/open-meteo/open-meteo`
- Commit: `d91c52f00665bb8ddd348f688fece556c933ffbb`
- Commit subject: `fix: missing pressure level in flatbuffers encoding (#1926)`
- Imported for: Open-Meteo server engine, model readers, API semantics,
  interpolation, weather-code logic, and GFS download/processing source paths.

## `vendor/openmeteo-sdk`

- Upstream: `https://github.com/open-meteo/sdk`
- Commit: `e6274cf9e10240b98219b16cf63cf0fae73347d9`
- Commit subject: `fix: bump release`
- Imported for: generated API schema, unit precision, and SDK serialization
  behavior used by Open-Meteo outputs.

## Update Rule

Before changing either vendored tree:

1. Record the old and new commits.
2. Explain why the public Open-Meteo API/source behavior requires the change.
3. Re-run point and layer validation in the 50 -> 100 -> 500 point order with
   50 forecast frames per point.
