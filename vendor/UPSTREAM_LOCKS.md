# Vendored Upstream Locks

The vendored upstream directories are plain source copies, not git submodules.
Do not update them without updating this file and `../UPSTREAM.md`.

## `vendor/open-meteo`

- Upstream: `https://github.com/open-meteo/open-meteo`
- Commit: `4efb9c49fb4a3718ed385fb22580d2e0fc56bdb2`
- Commit subject: `fix: weather codes slightly less thunderstorm lat scaling`
- Imported for: Open-Meteo server engine, model readers, API semantics,
  interpolation, weather-code logic, and GFS download/processing source paths.

## `vendor/openmeteo-sdk`

- Upstream: `https://github.com/open-meteo/sdk`
- Commit: `a29c4b62dd8445128e6db30f0a6fb5509fa1259c`
- Commit subject: `fix: use java version 17 for build (#262)`
- Imported for: generated API schema, unit precision, and SDK serialization
  behavior used by Open-Meteo outputs.

## Update Rule

Before changing either vendored tree:

1. Record the old and new commits.
2. Explain why the public Open-Meteo API/source behavior requires the change.
3. Re-run point and layer validation in the 50 -> 100 -> 500 point order with
   50 forecast frames per point.
