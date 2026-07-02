# Weather Forecast Server

This repository is the public AGPL weather forecast server for the Singapore
weather-model node.

The migration target is to use the Open-Meteo engine directly instead of
maintaining a Python reimplementation of Open-Meteo weather logic. Our local
code is limited to:

- China and surrounding-region domain selection.
- Source-download configuration for lightweight regional data serving.
- Point-package and layer-product export formats used by our clients.
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

## Current Scope

Singapore keeps weather-model processing only. Satellite code is intentionally
excluded because it has been split to another server.

The implementation order is:

1. Vendor and document the Open-Meteo engine baseline.
2. Configure Open-Meteo raw source ingestion with only the required
   region/variable boundary patches.
3. Export point API packages from Open-Meteo-derived data.
4. Export layers from the same Open-Meteo-derived data.
5. Deploy to Singapore and remove old satellite code/tasks there.
6. Validate 100 batches of 10 unique points x 24 consecutive hourly frames for
   the client-used GFS/CAMS point and layer variables.

## Layer Export

GFS and CAMS WebP layers are generated from the local Open-Meteo API, not from
the old Python GRIB parsing chain or precomputed point `.bin` packages. Point
weather queries should read the same Open-Meteo `.om` runtime through the local
API, so the production layer flow only builds map-layer products. The reusable
API-backed layer encoder remains `scripts/build_openmeteo_layers.py`.

Before serving or exporting products, generate the local Open-Meteo `.om`
runtime data from source files. The GFS point API uses Open-Meteo's `gfs_global`
mixer, so both `gfs013` and `gfs025` must be present locally. `gfs025` supplies
variables missing from GFS013 sflux files, including visibility and several
weather-code dependencies.

CAMS production is selected explicitly with `WEATHER_CAMS_SOURCE`. Use `ftp`
for ECMWF CAMS FTP/ECPDS production and `ads` only for the ADS/CDS backup path;
FTP/ECPDS runs are probed for remote file availability before production. The
scripts do not automatically switch sources after a failed FTP/ECPDS run. Put
real credential values in `config/singapore.private.env`; the tracked example
config only contains empty variable names.

For CAMS FTP/ECPDS, the scheduler can run every 30 minutes like GFS. It probes
the newest complete remote run first; ADS/CDS mode keeps the fixed UTC target
schedule because it is an API request path rather than direct file publication.

Point-output parity also requires Open-Meteo's Copernicus DEM90 static data for
land elevation correction. For production, keep the runtime data local and
preseed `copernicus_dem90/static/lat_*.om` files from a project-owned DEM
source.

```bash
bash scripts/download_openmeteo_runtime_data.sh
```

Write the source-derived GFS/CAMS API inventory:

```bash
python3 scripts/openmeteo_api_inventory.py \
  --output docs/validation/openmeteo-api-inventory.json
```

Run the required targeted point validation batches. The runner stops after 3
failed batches and writes per-batch reports:

```bash
python3 scripts/run_openmeteo_target_validation.py \
  --api-base-url http://127.0.0.1:18080 \
  --gfs-reference-base-url https://single-runs-api.open-meteo.com \
  --cams-reference-base-url https://air-quality-api.open-meteo.com \
  --gfs-run 2026-06-26T00:00 \
  --frames 24 \
  --batches 100 \
  --points-per-batch 10 \
  --output-dir docs/validation/reports
```

Build the server layer products used by production:

```bash
bash scripts/build_server_openmeteo_layers.sh
```

The server flow writes GFS WebP layers to
`data/openmeteo_layers/gfs013_surface` and CAMS WebP layers to
`data/openmeteo_layers/cams_global`. It does not build or publish
`point_weather.bin`, `pressure_profile.bin`, `point_package`, or
`pressure_profile_package`. It defaults to 121 hourly frames from the current UTC
hour and can be pinned with `WEATHER_OPENMETEO_LAYER_START_HOUR`,
`WEATHER_OPENMETEO_LAYER_END_HOUR`, `WEATHER_OPENMETEO_LAYER_FRAME_COUNT`, or
`WEATHER_OPENMETEO_GFS_RUN`.

Validate generated layers against the same Open-Meteo API before promotion:

```bash
python3 scripts/validate_openmeteo_layers.py \
  --layer-dir ./data/openmeteo_layers/gfs013_surface \
  --api-base-url http://127.0.0.1:18080/v1/forecast \
  --max-points 50 \
  --max-times 50
```

Run the current validation gate as 100 batches of 10 unique points x 24 frames.
Stop after 3 failed batches and record the changed revision, report, and
source-chain analysis before changing code again.

## Production Scheduling

Production scheduling is expressed in UTC only. Do not encode local server time
or region-specific time-zone names in scripts or crontab entries.

GFS uses a lightweight official-source probe every 30 minutes. The probe checks
NOAA GFS `.idx` files for `gfs013` sflux, `gfs025` pgrb2, and `gfs025` pgrb2b
through the configured forecast horizon. Only after a newer run is complete does
the GFS production cycle download source data, restart the local Open-Meteo API,
and rebuild the GFS WebP layers. While a GFS production cycle is still running,
later probe ticks skip instead of probing or starting another cycle:

```bash
bash scripts/run_gfs_probe_and_cycle.sh
```

CAMS global forecasts are checked only twice per day after the normal official
availability windows. The scheduled script computes the target `00Z` or `12Z`
run using UTC and starts CAMS production only when that run is not already
current:

```bash
bash scripts/run_cams_scheduled_cycle.sh
```

The production crontab should use:

```cron
CRON_TZ=UTC
*/30 * * * * WEATHER_FORECAST_APP_DIR=/opt/1panel/apps/weather_forecast_server /bin/bash /opt/1panel/apps/weather_forecast_server/scripts/run_gfs_probe_and_cycle.sh
30 10,22 * * * WEATHER_FORECAST_APP_DIR=/opt/1panel/apps/weather_forecast_server /bin/bash /opt/1panel/apps/weather_forecast_server/scripts/run_cams_scheduled_cycle.sh
```
