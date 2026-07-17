# Weather Forecast Server

This repository is the public AGPL weather forecast server for the Singapore
weather-model node.

The migration target is to use the Open-Meteo engine directly instead of
maintaining a Python reimplementation of Open-Meteo weather logic. Our local
code is limited to:

- China and surrounding-region domain selection.
- Source-download configuration for lightweight regional data serving.
- Layer-product export formats used by our clients.
- Deployment, scheduling, and validation tooling.

The previous private repository `89199156-design/weather_server_gfs` is
read-only migration input. It must not be modified or pushed during this
migration.

## License

This project is licensed under GNU Affero General Public License v3.0 or later.
The Open-Meteo upstream source is AGPL. Commercial use is allowed under the
license, but network use of modified server software requires source
availability for the corresponding modified version.

See [LICENSE](LICENSE) and [UPSTREAM.md](UPSTREAM.md).

The isolated deployment and acceptance sequence is documented in
[`docs/singapore-native-migration-runbook.md`](docs/singapore-native-migration-runbook.md).

## Current Scope

Singapore keeps weather-model processing only. Satellite code is intentionally
excluded because it has been split to another server.

The implementation order is:

1. Vendor and document the Open-Meteo engine baseline.
2. Configure Open-Meteo raw source ingestion with only the required
   region/variable boundary patches.
3. Publish immutable native Open-Meteo coverages with atomic group markers.
4. Add a native-runtime reader/manifest adapter, then let the shared Rust API
   and WebP repositories consume those coverages.
5. Deploy to Singapore and remove old satellite code/tasks there.
6. Validate exactly 2,000 reproducible random regional points over every
   published GFS/CAMS hour and three consecutive daily aggregation dates
   against Shanghai with strict output equality.

## Native OM Production

The GFS/CAMS OM cycle scripts are producer-only: they download original data,
let the vendored Open-Meteo Swift importer write native `.om` runtime data,
validate it, and atomically publish an immutable coverage. The scheduled entry
uses `run_native_model_pipeline.sh` to continue synchronously into Rust WebP
generation and one API reload event after the OM cycle returns successfully.

GFS keeps five consecutive source runs in one staging database:

1. The three oldest runs contribute forecast hours `0...5`.
   Each history member is a strict single-batch window; it never retains
   `f006` or substitutes an older cycle at the next run's `f000` boundary.
2. The immediately previous and newest runs are both retained through 384h.
   At query time the previous complete run is the same-valid-time fallback
   for a null value in the newest run.
3. The newest complete run extends the forecast through 384h.
   The 24-hour prefix always reaches UTC+8 local-day midnight for a 6-hour GFS
   cycle.

Already published source runs are hard-linked into staging. In a normal cycle,
the first two short runs and the previous newest complete run are reused after
their exact role-specific metadata and OM frame dimensions validate. The run
that rolls from the previous-complete position into the third short position is
re-imported at `0...5h`, and the new latest complete run is imported through
`f384`: exactly two downloads in the healthy rollover case. A cold start or a
gap imports every missing or invalid member with its required horizon. This relies on the upstream
full-run writer opening the run file with `overwrite: true` and a temporary
atomic replacement. Re-importing the immediately previous run with six
forecast times therefore replaces its old full-horizon `data_run` file with
exactly `f000...f005`; variables that officially omit `f000` store five frames,
and stale frames beyond `f005` cannot survive. During a same-run repair, a
validated full latest run is reused instead of downloaded again, `latest.json`
is restored from that run after history repair, and a revisioned immutable
coverage is published.

The official forecast schedule is hourly through 120h and 3-hourly from 123h
through 384h. A coverage is published only when both `ncep_gfs013` and
`ncep_gfs025`, including the configured pressure-level variables, pass that
schedule validation. The public window begins at the oldest retained run
(latest minus 24 hours), includes UTC+8 local-day midnight, and continues
through latest +384h: 408 hours / 409 hourly frames.

Run the producer-only cycles with:

```bash
bash scripts/run_gfs_om_production_cycle.sh YYYYMMDDHH
bash scripts/run_cams_om_production_cycle.sh YYYYMMDDHH
```

Build the patched Swift importer as a candidate image with:

```bash
bash scripts/build_openmeteo_image.sh
export WEATHER_OPENMETEO_TAG=native-REPLACE_WITH_PRINTED_SOURCE_ID
```

The default tag is derived from the actual Git revision plus local Docker/vendor
source diff and starts with `native-`. It does not update `latest`; setting
`WEATHER_OPENMETEO_TAG_LATEST=true` is an explicit production action. This
prevents a candidate build from overwriting the image used by the running
Singapore container.

The default output root is `data/om_producer`:

```text
coverages/<group>/<coverage_id>/
current/<group> -> immutable coverage
groups/<group>/releases/<release_id>.json
groups/<group>/current/ready_for_processing.json
staging/
```

The group ready marker is written last. Consumers must reload only after seeing
a new complete marker. Two immutable GFS rollback coverages and three CAMS
coverages are retained by default; this is separate from the model source-run
history inside each coverage. The producer does not impose a fixed free-space
threshold. Bounded retention and post-import cleanup control disk use.

The Singapore configuration limits Swift importer containers to 1.5 CPUs on
the current 2-vCPU host, with reduced CPU shares and low block-I/O weight so the
client-facing Rust API retains CPU and I/O headroom while a model cycle is
running. These controls are independent of disk capacity and do not reintroduce
a free-space threshold. Singapore sets Rust WebP to one worker and publishes
only after the complete immutable release is ready. Both limits remain
configurable if the host is upgraded.

### No local completion polling

GFS has a 6-hour source cadence and CAMS has a 12-hour source cadence. A remote
availability probe may run on a conservative retry schedule, but after it
selects a complete source run there must be no cron/timer that repeatedly asks
whether a local download, Swift import, WebP process, or API refresh has
finished. One locked process executes the batch as a return-code-driven chain:

```text
remote run ready -> download/import/validate OM -> atomic OM publish
                 -> render/validate/atomic WebP publish -> one systemd reload
```

If any command fails, the chain stops and existing API/WebP snapshots remain in
service. The Rust API has no 30-second reload timer and never rebuilds a
snapshot inside a client request. Its `SIGHUP` handler rebuilds in the
background only after the successful pipeline sends the single publish event.

CAMS retains three consecutive complete 12-hour runs in each coverage. A
normal cycle hard-links the previous coverage and downloads only the newest
full run; first start or a missing run downloads the missing members of the
three-run window. Before publication, both producers delete `data_run` run
directories outside their retained window and remove `download-*` plus
`http_cache`. GFS publication additionally proves from every `meta.json` and
OM file that the three historical runs contain exactly `0...5h` and both complete runs
contain the official `0...384h` schedule; CAMS proves all three runs
contain `0...120h`.

The publisher validates both the batch time metadata and each OM file's actual
stored time dimension. GFS latest-run metadata/files use the official 209-frame
source axis (`0...120` hourly, then `123...384` every three hours). CAMS batch
metadata is the 121-hour union, while real variable files are mixed: PM2.5,
PM10 and aerosol optical depth store 121 hourly frames; dust, CO, NO2, O3 and
SO2 store 41 three-hourly frames. Rust maps each file independently and only
interpolates the sparse variables at query/render time.

Each coverage marker also records `domain_grids` for every runtime domain. The
contract contains the actual cropped `nx`, `ny`, `lat_min`, `lon_min`, `dx`,
`dy`, source-grid offsets, and halo size computed with the same rules as the
vendored Swift domains. Rust consumers must use this contract; cropped runtime
dimensions must not be interpreted as the original global grid dimensions.

The current Shanghai Rust repositories read `.omranges` entries shaped as one
2D spatial array per variable and valid time. Native Open-Meteo runtime files
are time-series chunks, so they are not path-compatible with that reader even
though both use the OM file format. Do not point the Shanghai service directly
at `current/gfs`. The Rust integration must first add a native-runtime backend
or a deterministic manifest adapter that preserves the recorded regional grid
and time index. Weather formulas remain in the existing, baseline-locked Rust
implementation; the adapter only changes storage access.

Rust WebP remains a fixed 121-frame product for both GFS and CAMS: one file per
hour from the latest run at 0h through 120h. The longer 408h GFS and 144h CAMS
retained windows belong to OM/API and do not increase WebP file counts.

After a coverage is generated, validate it without replacing the production
API:

```bash
bash scripts/run_native_om_shadow_validation.sh gfs
bash scripts/run_native_om_shadow_validation.sh cams
```

This starts an ephemeral read-only container on `127.0.0.1:18081`, using the
exact image reference of the running production API. It verifies the marker,
immutable coverage pointer and retained run metadata. GFS checks both domains,
local-day midnight, latest run, 120/121/122/123h boundaries, and the 384h
endpoint. CAMS checks its three complete runs, local-day midnight, latest run,
and 120h endpoint. The script then stops the container and refuses to replace
any pre-existing shadow container.

The final parity gate compares the Shanghai service with the running Singapore
service using exactly 2,000 reproducible random points inside
`70..140E, 0..58N`. It covers the complete GFS horizon through f384, the
complete CAMS horizon through f120, and three consecutive daily dates from the
same GFS/CAMS source runs:

Before querying either API, generate `compare_model_run_identities.py inventory`
on both servers and run its `compare` command on the validation workstation.
The gate stops unless `latest_complete_run` matches for both GFS and CAMS; a
matching wall-clock query window alone is not accepted as same-batch evidence.

```bash
python3 scripts/compare_shanghai_singapore_api.py \
  --shanghai-url http://SHANGHAI_API \
  --singapore-url http://127.0.0.1:8088 \
  --gfs-run YYYYMMDDHH \
  --cams-run YYYYMMDDHH \
  --run-identity-report /tmp/shanghai-singapore-run-identity.json \
  --output-report data/validation/shanghai-singapore-2000-all-hours.json

python3 scripts/compare_shanghai_singapore_daily.py \
  --shanghai-url http://SHANGHAI_API \
  --singapore-url http://127.0.0.1:8088 \
  --gfs-run YYYYMMDDHH \
  --cams-run YYYYMMDDHH \
  --run-identity-report /tmp/shanghai-singapore-run-identity.json \
  --output-report data/validation/shanghai-singapore-2000x3-daily.json
```

The gate compares all published direct/derived surface variables, the 22-level
GFS pressure contract, CAMS/AQI variables, timestamps, units, nulls and JSON
numeric types. Only `generationtime_ms` is excluded because it is request
execution time. Any missing request, variable, hour, daily date or unequal
value fails the gate. Reduced point/hour/day counts require the explicit `--allow-reduced-test`
flag and are never accepted as production evidence. The live comparison uses
one worker and a 0.2-second inter-batch pause by default so validation does not
turn into a load test against the Shanghai client API.

On Singapore, the complete gate calls the real Rust service on its loopback
port, runs both native coverage validators, and then runs the mandatory
all-hour and three-day comparisons:

```bash
WEATHER_SHANGHAI_OM_API_URL=http://SHANGHAI_API \
WEATHER_OM_RUN_IDENTITY_REPORT=/tmp/shanghai-singapore-run-identity.json \
bash scripts/run_native_rust_api_parity_validation.sh
```

WebP parity is a separate exact-byte gate. Generate one read-only inventory on
each server (run with low CPU/I/O priority), copy only the two small inventory
JSON files to the validation workstation, and compare them there:

```bash
nice -n 15 ionice -c 3 python3 scripts/compare_webp_inventories.py inventory \
  --output-root /opt/1panel/apps/weather_om_webp/data \
  --output /tmp/webp-inventory.json

python3 scripts/compare_webp_inventories.py compare \
  --shanghai-inventory /tmp/shanghai-webp-inventory.json \
  --singapore-inventory /tmp/singapore-webp-inventory.json \
  --output-report /tmp/shanghai-singapore-webp-parity.json
```

The strict gate requires 2,178 GFS files (18 layers x 121 hours) and 484 CAMS
files (4 layers x 121 hours), 2,662 WebP files in total. Runs and normalized
manifests must match and every corresponding WebP SHA-256 must be identical.
`--allow-reduced-test` is diagnostic only and cannot satisfy production
acceptance.

## Legacy Layer Export

The former Python WebP builders and local Open-Meteo HTTP validation path remain
only as rollback and parity-test tooling. They are not called by the scheduled
native pipeline; the Rust API and WebP renderer are the production path. The
legacy layer scripts ultimately call
`scripts/build_webp.py`; this is documentation of the rollback path, not a
scheduled native-pipeline step.

Before serving or exporting products, generate the local Open-Meteo `.om`
runtime data from source files. The GFS point API uses Open-Meteo's `gfs_global`
mixer, so both `gfs013` and `gfs025` must be present locally. `gfs025` supplies
variables missing from GFS013 sflux files, including visibility and several
weather-code dependencies.

The standard CAMS Global air-quality domain uses ECMWF's authenticated ECPDS
distribution paths `CAMS_GLOBAL` and `CAMS_GLOBAL_ADDITIONAL`. Open-Meteo's
separate official `cams_global_greenhouse_gases` product uses the Copernicus ADS
dataset `cams-global-greenhouse-gas-forecasts`; production requests its
`carbon_monoxide` field for official API parity. The published CAMS group uses
the same two-UTC-day greenhouse release lag as the Open-Meteo bucket instead of
mixing a newer ADS cycle into an older CAMS release. Put real ECPDS and ADS
credentials only in `config/singapore.private.env` or a host `.cdsapirc`; the
tracked example config contains empty credential values.

No legacy layer scheduler is installed. Production scheduling is defined below:
one GFS trigger per six-hour source cycle and one CAMS trigger per twelve-hour
source cycle, with no local completion polling.

Point-output parity also requires Open-Meteo's Copernicus DEM90 static data for
land elevation correction. For production, keep the runtime data local and
preseed `copernicus_dem90/static/lat_*.om` files from a project-owned DEM
source.

The scheduled entrypoints call `scripts/run_native_model_pipeline.sh`; the
producer-only GFS runtime stage remains `scripts/run_gfs_om_production_cycle.sh`.
Generate scheduled CAMS runtime data with `scripts/run_cams_om_production_cycle.sh`.
The old `run_gfs_production_cycle.sh` and `run_cams_ftp_production_cycle.sh`
remain rollback-only.

Write the source-derived GFS/CAMS API inventory:

```bash
python3 scripts/openmeteo_api_inventory.py \
  --output docs/validation/openmeteo-api-inventory.json
```

Build the server layer products used by production:

```bash
bash scripts/build_openmeteo_gfs_layers.sh
bash scripts/build_openmeteo_cams_layers.sh
```

The server flow writes GFS WebP layers to
`data/webp/gfs013_surface` and CAMS WebP layers to
`data/webp/cams_global`. It defaults to 121 hourly frames from the current UTC
hour and can be pinned with `WEATHER_OPENMETEO_LAYER_START_HOUR`,
`WEATHER_OPENMETEO_LAYER_END_HOUR`, `WEATHER_OPENMETEO_LAYER_FRAME_COUNT`, or
`WEATHER_OPENMETEO_GFS_RUN`.

Validate generated layers against the same Open-Meteo API before promotion:

```bash
python3 scripts/validate_openmeteo_layers.py \
  --layer-dir ./data/webp/gfs013_surface \
  --api-base-url https://single-runs-api.open-meteo.com/v1/forecast \
  --max-points 50 \
  --max-times 50
```

The production acceptance gate is the strict 2,000-point, complete-hour and
three-day Shanghai/Singapore comparison documented above. Reduced batches are
diagnostic only and cannot satisfy deployment acceptance.

## Production Scheduling

Production schedules follow upstream UTC model cycles. The installed 1Panel
cron expressions are converted to the Singapore host's UTC+8 local time.

GFS uses one low-priority official-source probe per six-hour model cycle. The probe checks
only boundary/sentinel NOAA `.idx` files (`0,5,120,123,384h`) for `gfs013`
sflux, `gfs025` pgrb2, and `gfs025` pgrb2b. The actual import and publication
validators still require the complete real hourly/3-hourly schedule. Only after a newer run is
complete does the GFS producer hard-link the previous coverage into staging,
re-import the former previous-complete run at `0...5h`, retain the old latest
run through 384h, and import the new latest run through 384h,
validate the five-run rolling database, and publish a native OM coverage. On
first start, after a gap, or when legacy history overlaps through `f006`, it imports the
affected older `0...5h` runs from
the five-run window.
While a GFS production cycle is still running, later probe ticks skip instead of
probing or starting another cycle:

```bash
bash scripts/run_gfs_probe_and_cycle.sh
```

CAMS FTP/ECPDS uses one low-priority probe per twelve-hour model cycle and checks only the
first/final `0,120h` files for each configured variable. The importer still
validates the complete run before publication. It normally reuses two
historical full runs and downloads only the newest full run; first start or a damaged history causes the
missing runs in the three-run window to be downloaded. After that event,
the same cycle downloads only missing daily 00 UTC greenhouse runs from ADS.
That three-run window ends two UTC days before the CAMS run, matching the
official grouped release while preserving two prior complete fallback runs.
Both domains are then published atomically.
There is no separate high-frequency greenhouse poller.

```bash
bash scripts/run_cams_ftp_scheduled_cycle.sh
```

The production crontab should probe only once per upstream model cycle, after
the complete forecast horizon is normally available. In UTC, use:

```cron
CRON_TZ=UTC
17 4,10,16,22 * * * WEATHER_FORECAST_APP_DIR=/opt/1panel/apps/weather_forecast_server nice -n 15 ionice -c 3 /bin/bash /opt/1panel/apps/weather_forecast_server/scripts/run_gfs_probe_and_cycle.sh
37 8,20 * * * WEATHER_FORECAST_APP_DIR=/opt/1panel/apps/weather_forecast_server nice -n 15 ionice -c 3 /bin/bash /opt/1panel/apps/weather_forecast_server/scripts/run_cams_ftp_scheduled_cycle.sh
```

When code is deployed from an immutable release checkout, install with
`WEATHER_FORECAST_APP_DIR=/home/ubuntu/weather_releases/main`; the generated
jobs export that code root while keeping the private environment and OM data
under `/opt/1panel/apps/weather_forecast_server`.

The 1Panel installer stores equivalent UTC+8 local-time schedules
(`17 0,6,12,18 * * *` and `37 4,16 * * *`). Download, OM conversion, WebP
generation, and the single API reload remain one event-driven process chain;
no cron job polls local completion state. The installer reloads the 1Panel
scheduler once after its database transaction so an older in-memory schedule
cannot keep firing until the next machine reboot; weather data services are not
restarted.
