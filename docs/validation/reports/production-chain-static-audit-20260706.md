# Production Chain Static Audit 2026-07-06

## Runtime HTTP Removal

Search scope:

- `scripts`
- `vendor/open-meteo/Sources/App`
- `docker`
- `config`
- `README.md`
- `UPSTREAM.md`
- `tests`

Findings:

- Production scripts and layer builders do not call `127.0.0.1`, `/v1/forecast`, or `/v1/air-quality`.
- `/v1/forecast` and `/v1/air-quality` references remain only in validation tooling, inventory tooling, tests, and README validation examples.
- Upstream Open-Meteo comments and sync/S3 helper examples mention local HTTP addresses, but those are not used by the production GFS/CAMS build scripts.

Conclusion: the current GFS/CAMS production layer flow uses non-HTTP Open-Meteo export commands, not a local internal HTTP API hop.

## GFS Run Binding

Current scripts:

- `scripts/run_gfs_probe_and_cycle.sh`
- `scripts/run_gfs_production_cycle.sh`
- `scripts/download_openmeteo_gfs_data.sh`

Observed behavior:

- The probe checks official NOAA `.idx` availability for GFS013 sflux, GFS025 pgrb2, and GFS025 pgrb2b before starting production.
- `read_local_latest()` in `probe_gfs_official_run.py` returns the minimum latest run across `ncep_gfs013` and `ncep_gfs025`, so a newer run is considered only when both domains should advance together.
- `run_gfs_production_cycle.sh` downloads into a staging data directory first.
- Before publish, `validate_openmeteo_latest_run.py` requires both `ncep_gfs013` and `ncep_gfs025` at the requested run and requires configured pressure-level directories.
- Publish moves both domains and their `data_run` directories in one publish step. On failure after publish starts, the backup restore path restores both domains and the GFS layer directory.

Conclusion: current GFS production is bound across the 0.117-degree and 0.25-degree data paths; it does not publish a mixed-run GFS013/GFS025 product.

## CAMS FTP Hourly Download

Current files:

- `scripts/probe_cams_ftp_run.py`
- `scripts/download_openmeteo_cams_data.sh`
- `vendor/open-meteo/Sources/App/Cams/CamsDomain.swift`
- `vendor/open-meteo/Sources/App/Cams/CamsDownload.swift`

Observed behavior:

- `CamsDomain.cams_global.forecastHours` is `121`.
- `CamsDomain.cams_global.dtSeconds` is `3600`.
- `CamsDownload.swift` builds `timestamps = (0..<domain.forecastHours).map { run.add(hours: $0) }`, so FTP/ECPDS download writes continuous hourly frames from forecast hour 0 through 120.
- Multi-level CAMS variables use `CAMS_GLOBAL_ADDITIONAL` and the same hourly loop.
- The current FTP/ECPDS probe checks every requested forecast hour by default.
  With the production default `max_forecast_hour=120`, this checks forecast
  hours `0...120` for every configured CAMS variable before production starts.
- The full-hour probe is rate-safe: URLs are submitted in batches of at most
  `workers` requests and the probe stops scheduling additional URLs as soon as
  any batch reports a missing or rate-limited file. This prevents an incomplete
  remote run from causing a burst across all 121 hours.

Conclusion: CAMS FTP/ECPDS production download writes hourly frames, including multi-level variables. The scheduled probe now requires every configured hourly file to be present before starting production.

## Current Server State

Singapore server HEAD at audit time: `df0494d`.

Cron:

```cron
CRON_TZ=UTC
*/20 * * * * root cd /opt/1panel/apps/weather_forecast_server && bash scripts/run_gfs_probe_and_cycle.sh
*/20 * * * * root cd /opt/1panel/apps/weather_forecast_server && bash scripts/run_cams_ftp_scheduled_cycle.sh
```

Latest production data:

- GFS013: `2026-07-06T06:00:00Z`
- GFS025: `2026-07-06T06:00:00Z`
- CAMS FTP/ECPDS: `2026-07-06T00:00:00Z`

Latest probes:

- GFS `2026-07-06T12Z` not ready: NOAA `pgrb2b.0p25.f001.idx` returned `404`.
- CAMS FTP/ECPDS `2026-07-06T12Z` not ready: `CAMS_GLOBAL_ADDITIONAL` `no2` model-level file returned `404`.
