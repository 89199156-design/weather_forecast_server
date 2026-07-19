# Singapore native OM migration runbook

This runbook is for the Singapore node only. Shanghai is a read-only parity
baseline. During the current development phase, Singapore is validated in the
real production paths and ports because no client traffic is present. Published
snapshots and the previous immutable API/WebP binary releases remain available
for rollback.

## Invariants

- OM is published below the real Singapore producer root.
- API and WebP validation uses the real Singapore services and paths.
- The previous Singapore API/WebP release targets are preserved until the new
  binaries pass health and parity gates.
- No fixed free-disk threshold is used.
- No local process/directory completion polling is installed.
- A batch is one synchronous chain: OM publish, WebP publish, one API reload.
- Scheduled checks only probe small remote sentinel files. They never scan OM
  data, inspect local processes, call the client API, or rebuild an API index.
- A source run already present in the complete group marker is a no-op. The
  same GFS/CAMS run must not be regenerated merely because the probe ran again.
- GFS keeps five runs: three strict `f000...f005` histories followed by the
  previous and latest complete official `f000...f384` runs. No older `f006`
  value is mixed into the next run's `f000`.
- CAMS keeps three complete runs through `f120`.
- Failure preserves the previous immutable OM/API/WebP snapshots.

## 1. Prepare production artifacts

Build the patched Swift image with a `native-*` source-identity tag. Do not tag
or replace `latest`. Build the Rust `om-api` and `om-webp` from this repository
and build the official
`libomfileformat.so` artifacts in Linux. Record SHA-256 for all artifacts.
Build the decoder at the exact `om-file-format` revision pinned by the vendored
Open-Meteo `Package.swift`; an unpinned repository HEAD is not acceptable. Keep
the generated `libomfileformat.build.json` beside the shared library.

`om-webp` uses the sibling `../om_api` path from this repository, so both
binaries must be built from the same checked-out Git revision. Record that
single repository revision, the Swift image identity, decoder revision and the
four resulting artifact hashes in the acceptance evidence; binaries built from
different source revisions are invalid.

Reference the existing private CAMS credential file without printing or copying
its values into logs.
The current Singapore host has two vCPUs and supports the `x86-64-v3`
instruction set used by the pinned decoder. Configure Swift
containers for at most 1.5 CPUs, reduced CPU shares and low block-I/O weight;
configure WebP for one worker. These limits preserve client API headroom and
may be raised only after a host upgrade and a latency check.

## 2. Production API contract

The Rust production API reads the real producer root, accepts `SIGHUP` reloads,
and has no refresh timer. Client requests read the current immutable in-memory
snapshot only; they never scan the producer directory or wait for a refresh.

## 2a. Batch trigger contract

GFS is a six-hour product and CAMS is a twelve-hour product. The scheduler probes
once per source cadence, after the full forecast is normally available. That
probe reads only remote boundary/sentinel objects and the small local current
marker. It must
exit without starting Swift, WebP, or API reload when no newer complete run is
available.

Once a new run is selected, one locked foreground process owns the whole chain:

```text
remote ready -> download/import/validate OM -> atomic OM publish
             -> render/validate/atomic WebP publish -> one API SIGHUP
```

Each stage starts only after the preceding command exits successfully. There is
no separate high-frequency watcher for download completion, Swift completion,
WebP completion, or API refresh. A failed stage stops the chain and leaves the
previous immutable snapshots serving clients.

## 2b. GFS regional-download contract

Use NOAA's official endpoints for distinct purposes:

- Read the small GFS `.idx` inventories from the public
  `noaa-gfs-bdp-pds` S3 bucket.
- Download the actual GRIB payload from NOMADS Grib Filter with the storage
  halo `69-141E, -1-59N` and the required variable/level selection.
- Do not use S3 full-object downloads for production spatial cropping. HTTP
  byte ranges can select complete GRIB messages, but each selected message
  still contains the global grid.
- Keep at least 10 seconds between new NOMADS filter fetches, as required by
  the NOMADS Grib Filter automation guidance. A cache hit does not issue a
  remote fetch and therefore does not wait.

The S3 inventory and NOMADS filter response refer to the same official GFS
run/object family. This split avoids repeatedly querying the NOMADS inventory
edge while preserving server-side spatial cropping and the production disk
budget.

## 3. Select same source runs as Shanghai

Read the two Shanghai group markers and record `latest_complete_run` for GFS
and CAMS. Run the real Singapore GFS/CAMS pipelines manually for those exact
latest runs. Do not allow scheduled jobs to start a newer batch during this
same-run comparison stage.

The GFS cycle validates every seeded run against its assigned role before
reuse. During a healthy rollover it reuses two short runs and the old latest
complete run, reduces the former previous-complete run to strict
`f000...f005`, and downloads the new latest complete run. It therefore performs
two downloads; a cold start downloads all five missing roles. It restores both
domain `latest.json` files after any history repair,
validates every required surface and 22-level pressure file, and removes
raw/cache data after successful publication. If no explicit revision is set,
same-run repair publishes the distinct `three-short-two-full-v1` coverage rather than
colliding with the existing immutable coverage. The CAMS cycle imports missing
members of the latest three-run window, validates 121 direct hourly frames for
every main forecast variable and 41 native three-hour frames for the separate
greenhouse product, and then removes raw/cache data.

The greenhouse ADS request performs server-side cropping to the configured
storage bounds. The official CAMS Global ECPDS distribution consists of static
global NetCDF files and exposes no bounding-box request, so those files are
downloaded one at a time and cropped in the Swift importer before OM output;
they are never retained after the successful atomic publication.
`WEATHER_CAMS_FORCE_GREENHOUSE_DOWNLOAD=true` is reserved for a one-time
same-run source-path repair; normal cycles leave it false and download only
missing greenhouse batches.

`uv_index_clear_sky` is a required official GFS `CDUVB` field because the API
also exposes `uv_index_clear_sky_max`. For a one-time upgrade of retained runs,
use `WEATHER_OM_GFS_FORCE_REUSED_DOWNLOAD=true`,
`WEATHER_OM_GFS_REPAIR_SURFACE_ONLY=true`,
`WEATHER_OM_GFS_COVERAGE_REVISION=uv-clear-v1`,
`WEATHER_GFS013_SURFACE_VARIABLES=uv_index_clear_sky`, and
`WEATHER_GFS_SKIP_GFS025=true`. These are manual repair flags, not scheduler
settings. Partial metadata is merged with the retained run before the final
complete-coverage validation and atomic publish.

## 4. Prove run identity

Generate `compare_model_run_identities.py inventory` on each server and copy
only the JSON inventories to the validation workstation. Run its `compare`
command. Both `matched_latest_runs.gfs` and `matched_latest_runs.cams` must be
present and the report must have `passed=true`.

## 5. API parity gate

Use SSH tunnels to both real API services without exposing a new public port.
Run the parity launcher with the passed run-identity report. The mandatory gate
requires both APIs' open coverage files to match their on-disk publication
markers; matching marker text alone is rejected. This prevents a process that
still holds deleted files from an older immutable snapshot from being labelled
as the newer run during acceptance. The mandatory gate
uses 2,000 reproducible points and the complete published hourly horizon: GFS
from the Shanghai local-day start through `f384`, and CAMS from `f000` through
`f120`. It compares all canonical fields shared by the two public APIs: 222 GFS variables
(46 surface/derived fields plus eight families at all 22 pressure levels) and
19 CAMS/Chinese-AQI variables. A preflight checks every public variable batch
against both APIs before the 2,000-point run. The complete axis is requested
in bounded 200-point, 10-variable and 12-hour blocks so Shanghai never has to
materialize an oversized response. Every completed block is atomically
checkpointed and an interrupted acceptance run resumes only the missing
blocks; the final report still requires the union of all blocks to cover the
complete shared horizon. A second gate
compares all 57 supported GFS daily fields and 11 CAMS Chinese-AQI daily fields
for three consecutive `Asia/Shanghai` calendar days. Only
`generationtime_ms` is excluded. Both validators use one worker and an
inter-batch pause; they are one-time development acceptance jobs, never
scheduled production polling.

## 6. WebP parity gate

Generate strict WebP inventories on both servers with low `nice`/`ionice`
priority. Compare 2,178 GFS plus 484 CAMS files. The normalized manifests, runs,
relative paths and every WebP SHA-256 must match. A reduced inventory is never
production evidence. Copy the Shanghai inventory to Singapore and set
`WEATHER_SHANGHAI_WEBP_INVENTORY` before running the parity
launcher; the launcher must exit nonzero unless the exact WebP comparison also
passes.

## 7. Acceptance

Do not accept the migration unless all native coverage reports, same-run
identity report, API report and WebP report pass. Save the reports and artifact
hashes. Install low-priority GFS six-hour and CAMS twelve-hour remote-sentinel
jobs, then monitor latency/errors. Keep the previous service and WebP release
available for rollback until retirement is explicitly authorized.

## 8. Cleanup

Raw downloads are removed by successful model cycles. The GFS, CAMS main, and
greenhouse source-range debug caches are disabled by default, preventing a full
horizon from accumulating tens of gigabytes before the command-level cleanup
point. Docker build cache or obsolete images may be reclaimed only after an
explicit cleanup approval; active images and rollback releases are excluded.
