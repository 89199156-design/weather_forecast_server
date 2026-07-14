# Singapore native OM migration runbook

This runbook is for the Singapore node only. Shanghai is a read-only parity
baseline. During the current development phase, Singapore is validated in the
real production paths and ports because no client traffic is present. Published
snapshots and the previous API container remain available for rollback.

## Invariants

- OM is published below the real Singapore producer root.
- API and WebP validation uses the real Singapore services and paths.
- The old Singapore API container is preserved and is not modified or deleted.
- No fixed free-disk threshold is used.
- No local process/directory completion polling is installed.
- A batch is one synchronous chain: OM publish, WebP publish, one API reload.
- Scheduled checks only probe small remote sentinel files. They never scan OM
  data, inspect local processes, call the client API, or rebuild an API index.
- A source run already present in the complete group marker is a no-op. The
  same GFS/CAMS run must not be regenerated merely because the probe ran again.
- GFS keeps five runs: four strict `f000...f005` single-batch histories plus
  the latest official `f000...f384`. No older `f006` value is mixed into the
  next run's `f000`.
- CAMS keeps three complete runs through `f120`.
- Failure preserves the previous immutable OM/API/WebP snapshots.

## 1. Prepare production artifacts

Build the patched Swift image with a `native-*` source-identity tag. Do not tag
or replace `latest`. Build the Rust `om-api`, `om-webp` and official
`libomfileformat.so` artifacts in Linux. Record SHA-256 for all artifacts.
Build the decoder at the exact `om-file-format` revision pinned by the vendored
Open-Meteo `Package.swift`; an unpinned repository HEAD is not acceptable. Keep
the generated `libomfileformat.build.json` beside the shared library.

Publish or otherwise freeze the final `om-api` source revision before building
`om-webp`, then pin WebP's `om-api` dependency to that exact revision. A local
path override is allowed only for workstation tests and must not remain in the
production source tree. Record the Swift source identity, API Git revision,
WebP Git revision, decoder revision and the four resulting artifact hashes in
the acceptance evidence; a binary built from an older API revision is invalid.

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

The GFS cycle validates seeded history before reuse, repairs overlapping
`f000...f006` histories to strict `f000...f005`, and reduces the immediately
previous full run to those six forecast times. It reuses a validated same-batch latest full
horizon, restores both domain `latest.json` files after any history repair,
validates every required surface and 22-level pressure file, and removes
raw/cache data after successful publication. If no explicit revision is set,
same-run repair publishes the distinct `single-batch-f5-v1` coverage rather than
colliding with the existing immutable coverage. The CAMS cycle imports missing members of the latest
three-run window, validates the mixed 121/41-frame variable contract, and then
removes raw/cache data.

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
uses 2,000 reproducible points and the complete published hourly horizon: GFS
from the Shanghai local-day start through `f384`, and CAMS from `f000` through
`f120`. It compares 186 GFS variables and 39 CAMS/AQI variables. A second gate
compares all 61 supported GFS daily fields and 11 CAMS Chinese-AQI daily fields
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

Raw downloads and HTTP cache are already removed by successful model
cycles. Docker build cache or obsolete images may be reclaimed only after an
explicit cleanup approval; active images and rollback releases are excluded.
