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

During candidate API probing, the Open-Meteo process crashed when resolving
coordinates because the deployment env file passed
`WEATHER_DEM_REMOTE_DATA_DIRECTORY=` as an empty string. The upstream
Open-Meteo `configure.swift` contract allows the variable to be absent, or to
start with `http`, but not to be present and empty. The fix is limited to the
deployment shell wrapper: it writes a temporary Docker env file that drops empty
`KEY=` assignments before `docker run`. No Open-Meteo engine code was changed.

Later point validation showed that GFS weather values can still diverge on
land if the Copernicus DEM90 static files are absent. Without
`copernicus_dem90/static/lat_*.om` or
`WEATHER_DEM_REMOTE_DATA_DIRECTORY`, Open-Meteo falls back to model terrain for
the target elevation. That changes `elevation`, `surface_pressure`, and
elevation-sensitive derived outputs even when the GFS GRIB data and engine
logic match. Example after the failed 50-point gate:

- point `8.7,80.5`: local elevation `99m`, official elevation `92m`;
  local `surface_pressure` `997.9 hPa`, official `998.7 hPa`;
- after enabling the Open-Meteo DEM source for validation, the same point
  returned local elevation `92m` and `surface_pressure` `998.7 hPa`, matching
  official output.

The correct production fix is to provide the same Open-Meteo DEM90 static data
from an owned mirror, or pre-seed the local DEM static files. The vendored
weather reader and meteorology formulas remain unchanged.

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
- GFS point API surface variables: 86
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
  - supports `WEATHER_GFS_DOWNLOAD_MODE=sync` for point/API parity by using
    Open-Meteo's processed `.om` database from
    `WEATHER_OPENMETEO_SYNC_BASE_URL`;
  - keeps `WEATHER_GFS_DOWNLOAD_MODE=raw` only for raw-source debugging or
    approximate regional products;
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
  - downloads `cams_global` when CAMS credentials are configured;
  - generates the Docker env file from the effective `WEATHER_*` environment
    after config-file loading and explicit environment overrides, so owned
    mirror URLs passed at runtime are actually visible inside the Open-Meteo
    downloader container;
  - requires a DEM source before runtime data download unless
    `WEATHER_REQUIRE_DEM_SOURCE=false` is explicitly set.
- `vendor/open-meteo/Sources/App/Gfs/GfsDownload.swift`
  - uses Open-Meteo's HTTP/1.1 client and stable NOAA headers for GFS raw-data
    downloads only;
  - routes GFS025 `pgrb2b` files to `filter_gfs_0p25b.pl`.
- `vendor/open-meteo/Sources/App/Helper/Download/Curl.swift`
  - drains small non-success HTTP response bodies before retrying.
- `scripts/openmeteo_api_inventory.py`
  - writes the source-derived GFS/CAMS point API inventory;
  - keeps GFS point API validation on externally requestable
    `ForecastSurfaceVariable` names supported by the GFS reader, excluding
    GFS internal surface wind vector components that are raw download fields.
- `scripts/validate_openmeteo_point_api.py`
  - validates field presence, null coverage, and optional reference-value
    equality for GFS/CAMS point APIs;
  - uses the GFS-specific raw/derived Open-Meteo reader inventory for
    `models=gfs_global`, not the shared forecast enum that also contains
    non-GFS air-quality, marine, wave, and ensemble-spread names;
  - retries retryable reference/API failures such as official HTTP 429 rate
    limits so validation reports distinguish service throttling from value
    mismatches.
- `scripts/run_openmeteo_validation_gates.py`
  - runs 50, 100, then 500 point gates with 50 frames and stops on first
    failure;
  - passes point batch size and retry/pause controls to the validator so
    500-point gates do not require one HTTP request per point and official API
    throttling can be retried.
- `scripts/deploy_singapore_candidate.sh`
  - filters empty env-file assignments before `docker run` so optional upstream
    Open-Meteo variables remain absent instead of present with invalid empty
    values;
  - requires either an owned `WEATHER_DEM_REMOTE_DATA_DIRECTORY` mirror or
    pre-seeded local `copernicus_dem90/static/lat_*.om` files before deploying
    a parity candidate.
- `config/singapore.example.env`
  - documents `WEATHER_DEM_REMOTE_DATA_DIRECTORY` as required for land-point
    parity and adds `WEATHER_REQUIRE_DEM_SOURCE=true`.
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
  defaults, resume skip flags, and Docker env-file empty-value filtering;
- API inventory tests: passed;
- point API validation utility tests: passed;
- validation gate runner tests: passed.
- full Python test suite: `51 passed` after adding progress reports,
  proxy-free validation requests, fixed validation windows, and shared
  50-frame windows across point gates.

First Singapore candidate validation against official Open-Meteo stopped at
the required 50-point GFS gate:

- report:
  `docs/validation/reports/openmeteo-official-20260626T050457Z/50x50-gfs.json`
- checked values before stop: `664000`
- failures: `5441`
- primary failure classes: local 400 requests for validation chunks containing
  internal GFS raw surface variables, reference request failures, all-null
  variables, and value mismatches.

Root cause for the local 400 class: the validation inventory used
`GfsSurfaceVariable` directly as point API output. That enum contains
Open-Meteo downloader/reader internals such as `wind_u_component_10m`,
`wind_v_component_10m`, `categorical_freezing_rain`, and
`frozen_precipitation_percent`. The external `/v1/forecast?hourly=...` API is
driven by `ForecastSurfaceVariable`; GFS internal raw dependencies must not be
sent as public hourly parameters. The next validation round uses the corrected
source-derived point API inventory.

Second Singapore candidate validation against official Open-Meteo again stopped
at the 50-point GFS gate:

- report:
  `docs/validation/reports/openmeteo-official-20260626T051258Z/50x50-gfs.json`
- checked values before stop: `658000`
- failures: `5330`
- primary failure classes: reference mismatches, variables reported as
  all-null by the local validator, and reference request failures.

Root cause for the all-null class in the validator: local all-null series were
marked failed before comparing the reference series. For reference-backed
consistency validation, all-null is only a failure if the reference has values
or a different null pattern. The validator now compares local and reference
series first whenever a reference base URL is provided.

Third Singapore candidate validation against official Open-Meteo again stopped
at the 50-point GFS gate after the all-null comparison fix:

- report:
  `docs/validation/reports/openmeteo-official-20260626T061339Z/50x50-gfs.json`
- checked values before stop: `791000`
- failures: `5726`
- primary failure classes: reference mismatches and reference request failures;
  local request failures and all-null false positives were no longer present.

Root cause for the remaining value mismatch class: the official public forecast
API could not be pinned to a GFS run, and its `gfs025` data was one model run
behind the freshly downloaded Singapore data. Source/API inspection showed that
public `api.open-meteo.com`, `previous-runs-api.open-meteo.com`, and
`historical-forecast-api.open-meteo.com` reject `run`. The public
`single-runs-api.open-meteo.com` accepted `run`, but at the time of inspection
`ncep_gfs013` had `2026-06-25T18:00Z` while `ncep_gfs025` only had
`2026-06-25T12:00Z`. For the same sample point and valid time,
`api.open-meteo.com` `models=gfs025` matched `single-runs-api` 12Z values, not
the local 18Z download. `models=gfs013` matched 18Z on both local and official
API. The Open-Meteo engine/API reader path is therefore not the identified
cause; the mismatch is a domain-level data-run alignment issue.

Corrective change: keep the vendored Open-Meteo engine unchanged and add
domain-level run pinning to the runtime download wrapper only:

- `WEATHER_GFS013_RUN` can pin the `download-gfs gfs013` run;
- `WEATHER_GFS025_RUN` can pin both `gfs025` surface and pressure-level
  downloads;
- Docker download commands now use the same sanitized env-file handling as the
  candidate deploy script, preventing empty config values from overriding
  Open-Meteo defaults.

This makes official-API validation reproducible when the public API has
different latest runs per GFS domain, without forking the Open-Meteo weather
logic.

Fourth Singapore candidate validation against official Open-Meteo stopped at
the 50-point GFS gate after re-aligning GFS025 to the current official run:

- report:
  `docs/validation/reports/openmeteo-official-20260626T103046Z/50x50-gfs.json`
- checked values before stop: `761000`
- failure records: `5747`
- mismatched values: `22798`
- primary failure classes: `reference_mismatch` and official
  `reference_request_failed` HTTP 429.

Root cause for the largest deterministic mismatch class: missing Copernicus
DEM90 static data on the candidate. Land target elevation fell back to model
terrain, which shifted `surface_pressure` and elevation-sensitive derived
variables. After setting
`WEATHER_DEM_REMOTE_DATA_DIRECTORY=https://openmeteo.s3.amazonaws.com/data/`
for validation and restarting the candidate, sampled land points matched the
official API for `elevation`, `surface_pressure`, `dew_point_2m`,
`wet_bulb_temperature_2m`, and `apparent_temperature`. Production must use the
same DEM bytes from our mirror, not rely on the public Open-Meteo S3 endpoint.

Corrective change: keep the Open-Meteo engine unchanged and enforce the DEM
runtime data dependency in deployment/download wrappers. The validation tool
also retries official 429 responses so rate limiting does not hide the first
real value mismatch.

Follow-up small-sample probing after the DEM fix found a remaining GFS025
pressure-level wind mismatch at `46.98,126.7` for
`wind_u_component_300hPa`, `wind_v_component_300hPa`, and
`wind_speed_300hPa`. The same sample matched official `single-runs-api` 18Z
for temperature and geopotential height, so the issue was narrowed to a local
regional-filter download transform. The local filtered path had been rounding
decoded GRIB grid values to the message `decimalScaleFactor` before writing OM
files; upstream Open-Meteo does not do this pre-rounding. That local patch can
shift bilinear interpolation results by 0.1 to 0.33 units for continuous
fields. Corrective change: remove the pre-write rounding and preserve the
Open-Meteo/eccodes decoded values.

Fifth Singapore candidate validation stopped at the 50-point GFS gate after
the decoded-precision fix. The blocking deterministic mismatch was not an
Open-Meteo reader/API algorithm issue. It was isolated to the local NOAA
raw/filter data-production path for `ncep_gfs013` masked coastal variables.

Evidence:

- failed validation report:
  `docs/validation/reports/openmeteo-official-20260626T124608Z/50x50-gfs.json`
  with `398` mismatched values after `1,865,000` checked values;
- point `55.1,136.5` selected the same model coordinate locally and on the
  public API: approximately `55.11877,136.52344`, elevation `0m`;
- candidate data generated through NOAA raw regional conversion returned
  `snow_depth=null` from `2026-06-27T09:00Z`;
- a temporary Open-Meteo service using Open-Meteo's own `sync` command against
  the processed `ncep_gfs013/snow_depth/*.om` database returned
  `snow_depth=0.0` for the same point and frames;
- the public Open-Meteo API also returned `snow_depth=0.0` for the same point
  and frames.

Conclusion: strict point/API parity must use Open-Meteo's processed `.om`
database as the runtime data contract. NOAA raw GRIB download, especially with
regional subsetting and local domain-grid slicing, is useful for approximate
lightweight products but is not a valid parity baseline at masked coastal
cells. The engine should not be modified to compensate for those differences.

Corrective change: keep the vendored Open-Meteo reader and API engine intact
and add `WEATHER_GFS_DOWNLOAD_MODE=sync` to the runtime wrapper. In `sync`
mode, the wrapper calls Open-Meteo's native `sync` command for `ncep_gfs013`
and `ncep_gfs025` variables, using `WEATHER_OPENMETEO_SYNC_BASE_URL` as the
owned mirror of the processed Open-Meteo database. The previous NOAA raw
download path remains available only as `WEATHER_GFS_DOWNLOAD_MODE=raw` for
source-download diagnostics or non-parity regional products.

For lightweight Singapore deployment, prefer Open-Meteo's built-in
`REMOTE_DATA_DIRECTORY` reader pointed at the same owned processed `.om`
mirror, with bounded `CACHE_SIZE`/`CACHE_META_SIZE`. This preserves the
Open-Meteo runtime data contract without storing the full global GFS database
on the Singapore disk. The deployment wrappers must therefore pass
`REMOTE_DATA_DIRECTORY` and cache environment variables through to the
container; these variables are upstream Open-Meteo settings and do not start
with `WEATHER_`.

## Required Runtime Validation

After deploying the complete runtime data chain, run against a fixed reference
service that uses the same Open-Meteo source version and the same processed
`.om` data snapshot as the candidate:

```bash
python3 scripts/run_openmeteo_validation_gates.py \
  --api-base-url http://127.0.0.1:18080 \
  --reference-base-url http://127.0.0.1:18081 \
  --output-dir docs/validation/reports
```

Do not use the rolling public `api.open-meteo.com` endpoint as the large-sample
50/100/500 gate reference. It can be used only for small external spot checks.
The public endpoint is rate limited and its GFS data updates independently of
the public processed `.om` object store; a long gate can therefore compare
different data snapshots even when the candidate engine is unchanged.

Gate order:

1. 50 points x 50 frames for GFS and CAMS.
2. 100 points x 50 frames only if gate 1 passes completely.
3. 500 points x 50 frames only if gate 2 passes completely.

Failure rule:

- stop at the first failed scope;
- record report path, changed files, mismatch summary, and root-cause analysis;
- do not continue to the next point-count gate until the source/data issue is
  fixed and reviewed.

Sixth Singapore candidate validation used Open-Meteo's native remote
processed `.om` reader path for GFS, with the candidate reading the same
Open-Meteo processed data contract as the public API for validation. The
validation window was pinned once and reused for all local/reference requests:

- `start_hour`: `2026-06-26T10:00`
- `end_hour`: `2026-06-28T11:00`

The GFS 50-point gate passed:

- report:
  `docs/validation/reports/local-tunnel-20260626T090807Z/50x50-gfs.json`
- points x frames: `50 x 50`
- checked values: `1,865,000`
- completed chunks: `375 / 375`
- failures: `0`

The following 100-point gate did not complete. It stopped at the public
Open-Meteo reference API rate limit, not at a data mismatch:

- progress report:
  `docs/validation/reports/local-tunnel-20260626T090807Z/100x50-gfs.progress.json`
- checked values before stop: `1,672,000`
- completed chunks: `114 / 250`
- failure reason: `reference_request_failed`
- upstream response class: HTTP `429 Too Many Requests`

Root cause for this stop: both Singapore and local validation exits consumed
the public Open-Meteo daily API allowance while repeatedly running large
reference comparisons. The next validation run must use a fresh reference exit
or a controlled same-version reference service. The partial 100-point run has
no recorded value mismatch before the upstream rate limit.

A later Seoul-exit 100-point run used a deliberately different point sample
from the 50-point gate:

- report:
  `docs/validation/reports/seoul-100-different-20260626T115057Z/100x50-gfs.progress.json`
- point offset: `0.25`
- confirmed intersection with the 50-point sample: `0`
- checked values before stop: `1,270,000`
- completed chunks: `34 / 100`
- failure records: `1,755`
- mismatched scalar values inside those failed series: `71,870`
- dominant failed variable groups:
  - `geopotential_height`: `1,100` failed point-variable series;
  - `wind_v_component`: `632` failed point-variable series;
  - `cloud_cover`: `23` failed point-variable series.

Small-sample probes on failed points showed:

- candidate and official API chose the same model grid point and elevation;
- `models=gfs025` still mismatched, so the issue is not `gfs_global` domain
  mixing;
- ground-level fields such as `temperature_2m` and `weather_code` matched in
  the sampled failures;
- the mismatch concentrated in GFS025 pressure-level fields;
- a clean validation container reading the same public processed `.om` path
  returned the same pressure-level values as the candidate for sampled failed
  points.

Conclusion: the Seoul 100-point failure is a data-reference problem, not a
reason to change Open-Meteo's interpolation or weather-code engine. The rolling
public API and the public processed `.om` object store are not guaranteed to be
the same GFS025 pressure-level snapshot at validation time. Large-sample gates
must use a controlled same-snapshot reference service; public API comparison is
only a spot-check after the fixed-snapshot gates pass.

## Current Status

Do not describe the migration as a percentage complete. The auditable gate
state is:

- GFS point API: 50-point x 50-frame gate passed.
- GFS point API: 100-point x 50-frame gate against the rolling public API is
  invalid as a completion gate. It first hit HTTP 429 from public API quotas,
  then failed on a different point sample because the public API and public
  processed `.om` store were not on the same GFS025 pressure-level snapshot.
- Validation tooling now records point sampling offset so 50/100/500 gates can
  be proven to use distinct point sets.
- CAMS point API: not validated yet.
- Layer/API parity: not validated yet.
- Shanghai mirror and Android client path: not started for final migration.

## Correction: Upstream Engine Boundary

The earlier notes in this file mention local engine patches such as
`WEATHER_DEM_REMOTE_DATA_DIRECTORY`, `WeatherForecastServerSourceConfig`,
custom GFS download base URLs, NOAA filter overrides, and regional grid slicing
inside `GfsDomain.swift`/`GfsDownload.swift`. Those changes are no longer the
accepted direction.

Current rule:

- `vendor/open-meteo` must match the upstream Open-Meteo commit selected for
  current public API parity byte-for-byte. The active candidate is
  `acfb7eb13ffdca9d3772c57716c240d3a7d73da5`.
- Do not patch Open-Meteo model readers, grids, interpolation, weather-code
  derivation, downloader transport, API serialization, DEM handling, or SDK
  dependency wiring.
- Use upstream `REMOTE_DATA_DIRECTORY` for Open-Meteo processed data when remote
  reads are needed. In `single-runs` mode upstream derives `data_run` from that
  same URL.
- Keep public, commercial-safe, no-key Open-Meteo download/data sources
  unchanged. Replace a source only where the upstream dataset requires our
  authorization, credentials, or licensed mirror.
- China/surrounding-region bounds are product/export and mirror-selection
  bounds only. They must not change Open-Meteo source-grid semantics.
- Any owned regional mirror must preserve the upstream Open-Meteo `data/` and
  `data_run/` object layout and values for the selected model/window.

Validation after this correction must be run again from the rebuilt unmodified
engine image. Older 50-point pass records from the patched engine are retained
as historical evidence only and are not completion evidence.

The earlier `d91c52f00665bb8ddd348f688fece556c933ffbb` candidate included the
2026-06-15 upstream thunderstorm weather-code parameterisation changes
(`10e3cc39`/`2ccf980f`). A 10-point smoke test against the current
`single-runs-api.open-meteo.com` showed identical precipitation, showers, CAPE,
cloud, and visibility inputs but `weather_code` mismatches where the candidate
returned thunderstorm `95` and the public API returned drizzle `51/55`. The
selected `acfb7eb13ffdca9d3772c57716c240d3a7d73da5` commit is the upstream
commit immediately before that weather-code change and is therefore the next
current-API parity candidate. This is a source-version selection, not a local
weather-code patch.

## Current Target Validation Rule

The active completion gate is no longer the older `50/100/500 x 50-frame`
sequence.

Run targeted API parity in 100 batches:

- each batch contains 10 points;
- all 1000 points across the 100 batches must be unique;
- each point validates 24 consecutive hourly frames;
- 24 frames is only the validation window and must not change product/API
  output length;
- stop after 3 failed batches and analyze the failure cause before another
  modification round;
- validate only client-used GFS/CAMS layer variables and professional
  weather-app point outputs, not soil or unrelated specialist outputs.

GFS target variables:

- `temperature_2m`
- `relative_humidity_2m`
- `dew_point_2m`
- `apparent_temperature`
- `precipitation`
- `rain`
- `showers`
- `snowfall`
- `snow_depth`
- `weather_code`
- `visibility`
- `cape`
- `wind_speed_10m`
- `wind_direction_10m`
- `wind_gusts_10m`
- `wind_u_component_10m`
- `wind_v_component_10m`
- `cloud_cover`
- `cloud_cover_high`
- `cloud_cover_mid`
- `cloud_cover_low`
- `pressure_msl`
- `surface_pressure`
- `uv_index`
- `uv_index_clear_sky`
- `is_day`

CAMS target variables:

- `pm2_5`
- `pm10`
- `carbon_monoxide`
- `nitrogen_dioxide`
- `sulphur_dioxide`
- `ozone`
- `aerosol_optical_depth`
- `dust`
- `uv_index`
- `uv_index_clear_sky`
- `us_aqi`
- `european_aqi`

The ordinary public Open-Meteo API currently returns HTTP `429` with
`Daily API request limit exceeded. Please try again tomorrow.` from the Seoul
exit. Strict validation therefore uses `single-runs-api.open-meteo.com` with a
pinned `run=` value until the ordinary public API quota is available again.

## Target Validation Attempt: `f6cc85c` / `acfb7eb1`

Candidate:

- project commit: `f6cc85c5cba38cf9b67e891af8b34401a195f217`
- upstream Open-Meteo source: `acfb7eb13ffdca9d3772c57716c240d3a7d73da5`
- Singapore image: `weather-forecast-openmeteo:f6cc85c`
- image ID: `0c789a1de31f`
- candidate container: `09e46cdfa623`

Formal command:

```bash
python scripts/run_openmeteo_target_validation.py \
  --api-base-url http://127.0.0.1:18081 \
  --gfs-reference-base-url https://single-runs-api.open-meteo.com \
  --cams-reference-base-url https://air-quality-api.open-meteo.com \
  --reference-ssh-host seoul \
  --gfs-api-host-header single-runs-api.open-meteo.com \
  --gfs-reference-host-header single-runs-api.open-meteo.com \
  --cams-api-host-header air-quality-api.open-meteo.com \
  --cams-reference-host-header air-quality-api.open-meteo.com \
  --gfs-run 2026-06-26T06:00 \
  --start-hour 2026-06-26T06:00 \
  --batches 100 \
  --points-per-batch 10 \
  --frames 24 \
  --failure-limit 3 \
  --request-retries 1 \
  --request-retry-delay 2 \
  --request-pause 0.3 \
  --output-dir docs/validation/reports/target-f6cc85c-20260626T0600Z
```

Result:

- summary:
  `docs/validation/reports/target-f6cc85c-20260626T0600Z/summary-100x10x24.json`
- planned gate: `100` batches x `10` unique points x `24` frames;
- stopped after `3` completed batches because the failure limit was reached;
- completed points: `30 / 1000`;
- CAMS: `3` batches passed, `8640` checked values, `0` mismatches;
- GFS: `3` batches failed, `18720` checked values, `30` failed
  point-variable series;
- all GFS failures were `temperature_2m` reference mismatches;
- representative mismatch shape: local value is `0.1` C higher than
  `single-runs-api.open-meteo.com` on affected frames.

Additional spot checks:

- `weather_code` mismatch from the `d91c52f` candidate disappeared after
  switching to `acfb7eb1`;
- GFS `weather_code`, precipitation, rain, showers, snowfall, snow depth, CAPE,
  cloud cover, wind, visibility, pressure, UV, and day/night outputs did not
  produce failures in the three completed batches;
- querying `gfs_global` and `gfs013` separately showed the same temperature
  offset, while `gfs025` does not supply `temperature_2m` for this request;
- `cell_selection=nearest|land|sea` did not change either side;
- the same `0.1` C pattern reproduced on `2026-06-26T00:00` and
  `2026-06-26T06:00` runs.

Analysis:

The remaining blocker is not a local weather-code, interpolation, or variable
mapping patch. The vendored source now matches upstream `acfb7eb1`
byte-for-byte, and the failed value is the raw GFS013 `temperature_2m` API
output read from `REMOTE_DATA_DIRECTORY=https://openmeteo.s3.amazonaws.com/data/`.
The current public `single-runs-api.open-meteo.com` returns the same fields from
an internal reference path with `temperature_2m` rounded `0.1` C lower on many
frames. Since all related derived fields and CAMS are passing, the next fix must
focus on using the same GFS013 processed-data snapshot/source as the public API,
not on changing Open-Meteo engine logic.
