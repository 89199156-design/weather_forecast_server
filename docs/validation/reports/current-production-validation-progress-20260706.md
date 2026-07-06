# Current Production Validation Progress - 2026-07-06

## Server State

- Server: Singapore
- Repository HEAD on Singapore: `86b6428`
- Open-Meteo validation/runtime image used by current reports: `weather-forecast-openmeteo:313077c`
- GFS 0.117 domain latest: `2026-07-06T12:00:00Z`
- GFS 0.25 domain latest: `2026-07-06T12:00:00Z`
- CAMS global latest: `2026-07-06T00:00:00Z`
- GFS layer manifest batch: `1783339200`, 121 frames
- CAMS layer manifest batch: `1783296000`, 121 frames

## Scheduling And Source Separation

- `/etc/cron.d/weather-openmeteo` contains only:
  - `scripts/run_gfs_probe_and_cycle.sh` every 20 minutes
  - `scripts/run_cams_ftp_scheduled_cycle.sh` every 20 minutes
- No CAMS ADS/CDS cron entry exists on Singapore.
- `scripts/run_cams_ads_scheduled_cycle.sh` does not exist.
- Follow-up Singapore cron check on 2026-07-07 confirmed:
  - `CRON_TZ=UTC`
  - both installed cron entries are `*/20 * * * *`
  - GFS probe uses `flock` on probe, GFS cycle, and global production locks.
  - CAMS FTP schedule uses `flock` on schedule, CAMS FTP cycle, and global production locks.
  - CAMS ADS exists only as manual production/download scripts, not as a scheduled 20-minute probe path.
- Static code scan found no cross references between:
  - CAMS FTP production path and CAMS ADS/CDS backup path
  - GFS production path and CAMS production paths
- Local constraint tests passed:
  - `python -m pytest tests/test_deployment_scaffold.py tests/test_cams_ftp_probe.py tests/test_openmeteo_official_50point_batches.py`
  - Result: `65 passed`
- Follow-up local constraint tests passed on 2026-07-07:
  - `python -m pytest tests/test_deployment_scaffold.py tests/test_cams_ftp_probe.py tests/test_openmeteo_target_validation.py tests/test_openmeteo_api_inventory.py tests/test_vendor_integration.py -q`
  - Result: `67 passed`

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
- Official reference: Open-Meteo official API through Seoul/Shanghai/Singapore SSH exits
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
- Offset `300`: 3 batches, 150 points, 1,552,500 values, 0 mismatch
  - Report root: `docs\validation\reports\gfs-2026070606-pressure16-offset300-700x50-refsingapore-20260707T002343`
  - Stopped before batch 4 because Singapore official-reference exit returned `429 Hourly API request limit exceeded`.
- Offset `450`: 1 batch, 50 points, 517,500 values, 0 mismatch
  - Report root: `docs\validation\reports\gfs-2026070606-pressure16-offset450-550x50-reflocalvpn-20260707T005232`
  - Stopped before batch 2 because the local VPN official-reference exit returned `429 Too Many Requests`.
- Offset `500`: 3 batches, 150 points, 1,552,500 values, 0 mismatch
  - Report root: `docs\validation\reports\gfs-2026070606-pressure16-offset500-500x50-refsingapore-20260707T010058`
  - Stopped before batch 4 because Singapore official-reference exit returned `429 Minutely API request limit exceeded`.

Total current 06Z evidence:

- 13 batches
- 650 distinct points
- 50 frames per point
- 6,727,500 checked values
- 0 mismatch

Stop reason:

- Official API returned `429 Daily API request limit exceeded` through both Seoul and Shanghai reference exits.
- Singapore reference exit then completed 3 additional 50-point batches and stopped on `429 Hourly API request limit exceeded`.
- Local VPN reference exit then completed 1 additional 50-point batch and stopped on `429 Too Many Requests`.
- Singapore reference exit then completed 3 additional 50-point batches and stopped on `429 Minutely API request limit exceeded`.
- Follow-up checks at offset `650` did not add any batches:
  - Shanghai still returned `429 Daily API request limit exceeded`.
  - Seoul still returned `429 Daily API request limit exceeded`.
  - Local direct/VPN returned `429 Too Many Requests`.
  - v2rayN local proxy `127.0.0.1:10808` had exit IP `43.156.81.216` and also returned `429 Too Many Requests`.
- This is an external official API limit, not a local parity mismatch.

Next resume point:

- Continue at `--point-offset 650`
- Remaining target: 350 points, 7 batches
- The original `2026-07-06T06` local `.om` anchor has since been replaced on Singapore by the `2026-07-06T12` production batch. Do not mix new `12Z` results into the `06Z` total above unless the report is explicitly labelled as a separate `12Z` validation.

## GFS Official API Parity, Current 12Z Run

User-approved follow-up target:

- Local source: Singapore `.om`, direct mode
- Official reference: Open-Meteo official API
- GFS run: `2026-07-06T12`
- Start hour: `2026-07-06T12`
- Requested continuation point set: offset `650`, remaining 350 points

Fresh attempts:

- `7 x 50` via Singapore reference exit: stopped before adding a batch because the official API returned `429 Daily API request limit exceeded`.
- `1 x 50` via Seoul reference exit: stopped before adding a batch because the official API returned `429 Daily API request limit exceeded`.
- `1 x 50` via Shanghai reference exit: stopped before adding a batch because the official API returned `429 Daily API request limit exceeded`.
- `1 x 50` via local direct reference exit: stopped before adding a batch because the official API returned a non-JSON successful response during script execution; reproducing the first real 10-point request showed the official API returning `429 Daily API request limit exceeded`.
- `1 x 50` via local v2ray proxy `127.0.0.1:10808`: stopped before adding a batch because the official API returned a non-JSON successful response during script execution.
- A direct reproduction of the first real `10 points x 20 variables` official single-run request for offset `650` returned:
  - URL length: 767
  - HTTP status: `429`
  - Content-Type: `application/json; charset=utf-8`
  - Body: `{"error":true,"reason":"Daily API request limit exceeded. Please try again tomorrow."}`

12Z continuation result:

- Added batches: 0
- Added checked values: 0
- Stop reason: official API daily quota exhausted across all currently configured reference exits. This is not a local `.om` mismatch.
- Follow-up light probe on 2026-07-07 using the first real offset-650 request shape (`10 points x 20 variables`) still returned:
  - HTTP status: `429`
  - Content-Type: `application/json; charset=utf-8`
  - Body: `{"reason":"Daily API request limit exceeded. Please try again tomorrow.","error":true}`

Resume command template:

```powershell
$ts=(Get-Date -Format 'yyyyMMddTHHmmss')
$out="docs\validation\reports\gfs-2026070606-pressure16-offset650-350x50-ref<exit>-$ts"
python scripts\validate_openmeteo_official_50point_batches.py `
  --local-openmeteo-mode direct `
  --direct-ssh-host singapore `
  --direct-remote-root /opt/1panel/apps/weather_forecast_server `
  --reference-ssh-host <exit> `
  --scopes gfs `
  --openmeteo-image weather-forecast-openmeteo `
  --openmeteo-tag 313077c `
  --gfs-run 2026-07-06T06 `
  --gfs-start-hour 2026-07-06T06 `
  --cams-start-hour 2026-07-06T00 `
  --frames 50 `
  --batches 7 `
  --points-per-batch 50 `
  --point-offset 650 `
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

- Report: `docs/validation/reports/layers-gfs-2026070612-313077c-2000x121-localom-20260706T183324Z.md`
- Scope: GFS layers
- Points: 2000
- Frames: 121
- Layers: 18
- Checked values: 4,598,000
- Mismatch count: 0
- Source batch: `2026-07-06T12`

CAMS current layer validation:

- Report: `docs/validation/reports/layers-cams-2026070600-313077c-2000x121-localom-20260706T184931Z.md`
- Scope: CAMS layers
- Points: 2000
- Frames: 121
- Layers: 4
- Checked values: 968,000
- Mismatch count: 0
- Source batch: `2026-07-06T00`
- Validation export cleanup: `data/openmeteo/validation_layer_export` reduced to `4.0K`; production layer directories were left in place.

## CAMS Official API Parity Evidence

Current production-style 50-frame CAMS report:

- Report root: `docs/validation/reports/cams-production-dfce90a-1000x50-refshanghai-20260706T143309Z`
- Completed batches: 20
- Completed points: 1000
- Frames per point: 50
- Variables available for CAMS: 9
- Batch evidence: every `batch-*-cams.json` report is `passed: true`; sampled batches show `failed_points: 0` and empty `failures`.
- Summary result: `passed: true`

Earlier 121-frame CAMS parity evidence:

- First 50 points: `docs/validation/reports/cams-official-batch01-50x121-2026070412-30aca1e-localtunnel-strict-20260705T0225/summary-50x121.json`, `passed: true`
- Remaining 950 points: `docs/validation/reports/cams-official-remaining950x121-2026070412-30aca1e-localtunnel-strict-20260705T0226/summary-950x121.json`, `passed: true`
- Combined scope: 1000 unique points, 121 frames, 9 CAMS outputs.

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

- Finish GFS official parity for the remaining 350 current-run points after official API quota recovers or another reference exit is available.
- Run or refresh full current-batch layer-to-local-OM parity if production WebP output is regenerated after the recorded layer reports.
- Keep this report updated with final 1000-point results before claiming completion.
## Deployment image cleanup on 2026-07-07

Singapore private runtime config now pins `WEATHER_OPENMETEO_TAG=313077c`.
Old Open-Meteo image tags were removed from Singapore; remaining tags are:

- `weather-forecast-openmeteo:313077c`
- `weather-forecast-openmeteo:latest`

Both remaining tags point to the same image id (`f099e313b4a5`).
