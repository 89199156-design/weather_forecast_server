# Open-Meteo GFS/CAMS Consistency Record

Date: 2026-06-26

## Objective

Provide the same point API surface as the selected vendored Open-Meteo source
version for:

- `/v1/forecast` with `models=gfs_global`
- `/v1/air-quality` with `domains=cams_global`

The client may use only a subset of these fields, but the server must expose
the full GFS/CAMS point output set that the selected Open-Meteo API source
accepts.

## Commercial Compliance Boundary

This project directly vendors Open-Meteo under AGPL-3.0-or-later. Commercial
use is allowed only with the AGPL obligations preserved:

- keep the Open-Meteo license and upstream source records in the public repo;
- keep local patches auditable;
- do not place the Open-Meteo-derived service behind a closed-source wrapper;
- publish corresponding source for the running network service.

## Engine Modification Boundary

Open-Meteo engine behavior must remain Open-Meteo-owned. Local changes are
restricted to:

- configured GFS/CAMS source URLs;
- China and surrounding-region download clipping;
- Docker/deployment scripts;
- Shanghai mirror scripts;
- WebP layer product export;
- validation tools and reports.

Do not reimplement or locally fork:

- GFS/CAMS readers;
- weather-code calculation;
- interpolation;
- model fallback and mixing;
- unit conversion or API serialization semantics.

## Root-Cause Analysis

The visibility and weather-code dependency failures were traced to incomplete
runtime data, not to a missing Open-Meteo algorithm.

Evidence from vendored source:

- `ForecastapiController.swift` maps `gfs_global` to `GfsReader(domains:
  [.gfs025, .gfs013])`.
- `GfsVariableDownloadable.swift` maps `visibility`, `wind_gusts_10m`, `cape`,
  `lifted_index`, `convective_inhibition`, and `categorical_freezing_rain`
  to `gfs025` GRIB messages, not only to `gfs013` sflux.
- `GfsDownload.swift` downloads only surface variables by default.
  Pressure-level API variables therefore require separate `gfs025`
  `--only-variables` batches for the pressure-level raw names.
- `CamsReader.swift` derives AQI fields from raw CAMS fields, so CAMS parity
  requires `download-cams cams_global` with valid credentials.

Conclusion: the correct fix is to complete the Open-Meteo runtime data chain:
`gfs013` surface, `gfs025` surface, `gfs025` pressure-level batches, and
`cams_global`.

During Singapore runtime download, a single pressure-level filter request for
all `gfs025` variables returned repeated NOAA filter HTTP 500 responses. This
was caused by our orchestration asking the Open-Meteo downloader for too many
pressure-level filter items in one request. The fix keeps Open-Meteo's native
download and conversion path, but calls it in smaller `--only-variables`
batches by pressure variable family and pressure-level chunks.

Follow-up evidence from the same NOAA filter endpoint:

- single-level `var_TMP` requests for `850`, `70`, and `10` hPa returned HTTP
  200;
- a four-level `var_TMP` request for `1000`, `925`, `850`, and `700` hPa
  returned HTTP 200;
- the full-level `var_TMP` request returned HTTP 500 repeatedly.
- after chunking, a `temperature_40hPa,temperature_50hPa,temperature_70hPa,
  temperature_100hPa` request with download concurrency `4` reached NOAA
  HTTP 302 retry loops at forecast hour `42`;
- the same chunk with download concurrency `1` passed forecast hour `42` and
  converted all four variables successfully.
- a later full pressure-level run still hit repeated HTTP 302 on `.idx`
  requests. Container-level curl probes showed empty/headerless requests can
  receive repeated 302 responses from NOAA/Akamai, while normal curl-style
  requests intermittently recover with HTTP 200.
- after the HTTP transport patch, pressure-level downloads advanced to mixed
  `pgrb2`/`pgrb2b` chunks. NOAA filter returned HTTP 500 when a `pgrb2` filter
  URL included `pgrb2b`-only levels such as `125hPa` or `175hPa`; those levels
  returned HTTP 500 even as single-level `pgrb2` filter requests, while
  `150hPa` and `200hPa` returned HTTP 200.
- NOAA exposes `pgrb2b` through the secondary filter endpoint
  `filter_gfs_0p25b.pl`. Direct probes against that endpoint returned HTTP 200
  for pgrb2b pressure levels such as `125hPa`, `175hPa`, `225hPa`, and
  `275hPa`.

Conclusion: this is a download-request sizing issue in our orchestration. It is
not evidence of an Open-Meteo decoding, interpolation, weather-code, or API
logic mismatch. The local vendored patch is therefore restricted to the GFS
download transport path: HTTP/1.1 client, stable NOAA request headers, and
draining small non-success response bodies before retry. The runtime script
keeps Open-Meteo's `download-gfs` command, but groups pressure-level batches by
the underlying NOAA GRIB file family before chunking so filter URLs do not mix
`pgrb2` and `pgrb2b` levels. The GFS025 filter URL selector routes `pgrb2b`
files to NOAA's secondary filter endpoint.

## Source-Derived Inventory

The inventory must be generated from the selected vendored source:

```bash
python3 scripts/openmeteo_api_inventory.py \
  --output docs/validation/openmeteo-api-inventory.json
```

Inventory source locations:

- forecast surface API names: `ForecastSurfaceVariable`
- forecast pressure API names: `ForecastPressureVariableType` x
  `GfsDomain.gfs025.levels`
- GFS runtime raw variables: `GfsSurfaceVariable` and `GfsPressureVariableType`
- GFS point API variables used for validation: `GfsSurfaceVariable`,
  `GfsVariableDerivedSurface`, `GfsPressureVariableType`, and
  `GfsPressureVariableDerivedType`
- CAMS raw names: `CamsVariable`
- CAMS derived names: `CamsVariableDerived`

Generated inventory snapshot:

- forecast surface API variables: 379
- forecast pressure API variables: 660
- GFS runtime surface variables: 45
- GFS runtime pressure variables: 308
- GFS point API surface variables: 94
- GFS point API pressure variables: 660
- CAMS raw variables: 30
- CAMS derived variables: 21

Explicitly reviewed required examples:

- `uv_index`
- `uv_index_clear_sky`
- `visibility`
- `weather_code`
- `precipitation`
- `rain`
- `snowfall`
- `wind_speed_10m`
- `temperature_2m`
- `pm2_5`
- `pm10`
- `aerosol_optical_depth`
- `us_aqi`
- `european_aqi`

## Current Code Changes

- `scripts/download_openmeteo_runtime_data.sh`
  - downloads `gfs013` surface;
  - downloads `gfs025` surface;
  - downloads `gfs025` pressure-level variables in smaller Open-Meteo
    `--only-variables` batches by variable family and pressure-level chunk;
  - keeps `pgrb2` and `pgrb2b` pressure-level groups separate before chunking
    to avoid invalid NOAA filter URLs;
  - uses a separate pressure-level download concurrency default of `1` to keep
    NOAA/CDN redirects from breaking Open-Meteo's downloader retries;
  - supports runtime skip flags for already completed `gfs013`, `gfs025`
    surface, `gfs025` pressure-level, and CAMS download scopes;
  - sources the runtime env file before deriving download defaults;
  - downloads `cams_global` when CAMS credentials are configured.
- `vendor/open-meteo/Sources/App/Gfs/GfsDownload.swift`
  - uses Open-Meteo's HTTP/1.1 client and stable NOAA headers for GFS raw-data
    downloads only;
  - routes GFS025 `pgrb2b` files to `filter_gfs_0p25b.pl`.
- `vendor/open-meteo/Sources/App/Helper/Download/Curl.swift`
  - drains small non-success HTTP response bodies before retrying.
- `scripts/openmeteo_api_inventory.py`
  - writes the source-derived GFS/CAMS point API inventory.
- `scripts/validate_openmeteo_point_api.py`
  - validates field presence, null coverage, and optional reference-value
    equality for GFS/CAMS point APIs;
  - uses the GFS-specific raw/derived Open-Meteo reader inventory for
    `models=gfs_global`, not the shared forecast enum that also contains
    non-GFS air-quality, marine, wave, and ensemble-spread names.
- `scripts/run_openmeteo_validation_gates.py`
  - runs 50, 100, then 500 point gates with 50 frames and stops on first
    failure.
- `scripts/build_openmeteo_layers.py`
  - uses `gfs_global` by default for layer export;
  - adds Open-Meteo weather-code-based categorical layer products for
    weather code, precipitation phase, and thunderstorm.
- `scripts/validate_openmeteo_layers.py`
  - validates categorical layer products against the same Open-Meteo API
    field source.

## Tests Run

Local unit tests run during this record:

```bash
python -m pytest tests\test_deployment_scaffold.py -q
python -m pytest tests\test_openmeteo_api_inventory.py -q
python -m pytest tests\test_openmeteo_point_api_validation.py -q
python -m pytest tests\test_openmeteo_validation_gates.py -q
python -m pytest -q
```

Results observed:

- deployment scaffold tests: passed after adding `gfs025` pressure-level
  `--only-variables` batches, pressure-level chunking, env-file-first
  defaults, and resume skip flags;
- API inventory tests: passed;
- point API validation utility tests: passed;
- validation gate runner tests: passed.
- full Python test suite: `39 passed`.

## Required Runtime Validation

After deploying the complete runtime data chain, run:

```bash
python3 scripts/run_openmeteo_validation_gates.py \
  --api-base-url http://127.0.0.1:18080 \
  --reference-base-url http://127.0.0.1:18081 \
  --output-dir docs/validation/reports
```

Gate order:

1. 50 points x 50 frames for GFS and CAMS.
2. 100 points x 50 frames only if gate 1 passes completely.
3. 500 points x 50 frames only if gate 2 passes completely.

Failure rule:

- stop at the first failed scope;
- record report path, changed files, mismatch summary, and root-cause analysis;
- do not continue to the next point-count gate until the source/data issue is
  fixed and reviewed.

## Current Status

Manual source review has identified the missing data-chain requirements. Local
unit tests for the new tooling pass. Full runtime consistency has not yet been
claimed because the Singapore runtime data and same-version reference service
must still be deployed and validated.
