# Current Production Validation Progress - 2026-07-06

## Server State

- Server: Singapore
- Deployed commit: `db58a2b`
- GFS 0.117 domain latest: `2026-07-06T06:00:00Z`
- GFS 0.25 domain latest: `2026-07-06T06:00:00Z`
- CAMS global latest: `2026-07-06T00:00:00Z`
- GFS layer manifest batch: `1783317600`, 121 frames
- CAMS layer manifest batch: `1783296000`, 121 frames

## Scheduling And Source Separation

- `/etc/cron.d/weather-openmeteo` contains only:
  - `scripts/run_gfs_probe_and_cycle.sh` every 20 minutes
  - `scripts/run_cams_ftp_scheduled_cycle.sh` every 20 minutes
- No CAMS ADS/CDS cron entry exists on Singapore.
- `scripts/run_cams_ads_scheduled_cycle.sh` does not exist.
- Static code scan found no cross references between:
  - CAMS FTP production path and CAMS ADS/CDS backup path
  - GFS production path and CAMS production paths
- Local constraint tests passed:
  - `python -m pytest tests/test_deployment_scaffold.py tests/test_cams_ftp_probe.py tests/test_openmeteo_official_50point_batches.py`
  - Result: `65 passed`

## Internal HTTP And Legacy Bin Audit

- Local production-path scan found no `127.0.0.1:18080` or `localhost:18080` references in production scripts/config.
- Singapore has no listener on port `18080`.
- Singapore has no matching Open-Meteo internal HTTP/point-weather process.
- `/v1/forecast` and `/v1/air-quality` remain only in validation tooling and README examples, not in the production generation path.
- Singapore `data/` contains no legacy `point_weather.bin` / `pressure_profile.bin` / `*.bin` production artifacts.
- Production scripts do not create or publish legacy bin products.
- Remaining `.bin` references are either upstream Open-Meteo cache/docs/tests or local tests asserting legacy bin cleanup.

## GFS Official API Parity, Current 06Z Run

Validation target:

- Local source: Singapore `.om`, direct mode
- Official reference: Open-Meteo official API through Seoul/Shanghai SSH exits
- GFS run: `2026-07-06T06`
- Start hour: `2026-07-06T06`
- Frames per point: 50
- Points per batch: 50
- Variables compared: 207
- Pressure-level contract: default 16-level set in `scripts/validate_openmeteo_official_50point_batches.py`

Completed:

- Offset `0`: 1 batch, 50 points, 517,500 values, 0 mismatch
- Offset `50`: 2 batches, 100 points, 1,035,000 values, 0 mismatch
- Offset `150`: 3 batches, 150 points, 1,552,500 values, 0 mismatch

Total current 06Z evidence:

- 6 batches
- 300 distinct points
- 50 frames per point
- 3,105,000 checked values
- 0 mismatch

Stop reason:

- Official API returned `429 Daily API request limit exceeded` through both Seoul and Shanghai reference exits.
- This is an external official API limit, not a local parity mismatch.

Next resume point:

- Continue at `--point-offset 300`
- Remaining target: 700 points, 14 batches

Resume command template:

```powershell
$ts=(Get-Date -Format 'yyyyMMddTHHmmss')
$out="docs\validation\reports\gfs-2026070606-pressure16-offset300-700x50-ref<exit>-$ts"
python scripts\validate_openmeteo_official_50point_batches.py `
  --local-openmeteo-mode direct `
  --direct-ssh-host singapore `
  --direct-remote-root /opt/1panel/apps/weather_forecast_server `
  --reference-ssh-host <exit> `
  --scopes gfs `
  --openmeteo-image weather-forecast-openmeteo `
  --openmeteo-tag latest `
  --gfs-run 2026-07-06T06 `
  --gfs-start-hour 2026-07-06T06 `
  --cams-start-hour 2026-07-06T00 `
  --frames 50 `
  --batches 14 `
  --points-per-batch 50 `
  --point-offset 300 `
  --chunk-size 20 `
  --timeout 120 `
  --request-retries 1 `
  --request-retry-delay 2 `
  --request-pause 0.2 `
  --output-dir $out
```

Replace `<exit>` with a reference SSH host that is not currently API-limited.

## Layer To Local OM Parity

GFS current layer validation:

- Report: `docs/validation/reports/layers-gfs-2026070606-182f4f6-2000x121-localom-20260706.md`
- Scope: GFS layers
- Points: 2000
- Frames: 121
- Layers: 18
- Checked values: 4,598,000
- Mismatch count: 0

CAMS current layer validation:

- Report: `docs/validation/reports/layers-cams-2026070600-182f4f6-2000x121-localom-20260706.md`
- Scope: CAMS layers
- Points: 2000
- Frames: 121
- Layers: 4
- Checked values: 968,000
- Mismatch count: 0

## CAMS China AQI Direct Export Gate

Root cause found and fixed in `db58a2b`:

- The no-HTTP `export-point-forecast` command had regressed to `MultiDomains.getReader` for CAMS.
- That path did not strictly bind the requested `cams_global` domain and produced inconsistent CAMS direct-export values for China AQI checks.
- The fix restores direct `CamsDomain(rawValue: request.model)` binding for CAMS export only.
- This does not change the Open-Meteo weather or air-quality algorithms; it only fixes our internal no-HTTP export entrypoint.

Regression tests:

- `python -m pytest tests/test_deployment_scaffold.py tests/test_cams_ftp_probe.py tests/test_openmeteo_target_validation.py -q`
- Result: `52 passed`

Singapore deployment check after rebuilding `weather-forecast-openmeteo:db58a2b` and `latest`:

- Test point: `31.23, 121.47`
- Time: `2026-07-06T00:00Z`
- `pm2_5`: `21.0`
- `pm10`: `22.6`
- `nitrogen_dioxide`: `32.2`
- `ozone`: `53.0`
- `sulphur_dioxide`: `11.4`
- `carbon_monoxide`: `252.0`
- `ch_iaqi_pm2_5`: `30.0`
- `ch_iaqi_pm10`: `23.0`
- `ch_iaqi_no2`: `17.0`
- `ch_iaqi_o3`: `17.0`
- `ch_iaqi_so2`: `4.0`
- `ch_iaqi_co`: `3.0`
- `ch_aqi`: `30.0`

Gate result:

- `ch_aqi == max(ch_iaqi_*)`
- `latest` and `db58a2b` image tags both passed this check.

## Current Completion Status

Not complete.

Remaining mandatory items:

- Finish GFS official parity for the remaining 700 current-run points after official API quota recovers or another reference exit is available.
- Keep CAMS standard-output official parity evidence tied to the production FTP/ECPDS source.
- Keep this report updated with final 1000-point results before claiming completion.
