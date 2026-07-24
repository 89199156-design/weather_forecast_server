# Vendored Upstream Locks

The existing GFS/CAMS trees are plain source copies. The isolated ECMWF tree is
an exact Git submodule so its upstream identity is independently verifiable.
Do not update any upstream without updating this file and `../UPSTREAM.md`.

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

## `vendor/open-meteo-ecmwf`

- Upstream: `https://github.com/open-meteo/open-meteo`
- Commit: `b743cbc9a7fab3f8f7dda85968fb770eee48b9ec`
- Commit subject: `Add CycleWeather to list of apps using Open-Meteo (#2001)`
- Imported for: isolated ECMWF IFS 0.25° Open Data import, official ECMWF
  reader/derived/API behavior, and regional native OM production.
- Local patch: `../patches/open-meteo-ecmwf-regional.patch`
- Build image:
  `ghcr.io/open-meteo/docker-container-build@sha256:e0ef0354d44c4a9330eabe68be5b29cf303ca654444db4ae76f2b601ec161e6f`
- Runtime image:
  `ghcr.io/open-meteo/docker-container-run@sha256:7e6ee634cc774abdcf1875dc632229d51368a2b32e4714fed880c41bd7155aff`

## Update Rule

Before changing either vendored tree:

1. Record the old and new commits.
2. Explain why the public Open-Meteo API/source behavior requires the change.
3. Re-run point and layer validation in the 50 -> 100 -> 500 point order with
   50 forecast frames per point.
