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
2. Configure Open-Meteo raw source downloads without modifying the engine.
3. Export point API packages from Open-Meteo-derived data.
4. Export layers from the same Open-Meteo-derived data.
5. Deploy to Singapore and remove old satellite code/tasks there.
6. Validate 100 batches of 10 unique points x 24 consecutive hourly frames for
   the client-used GFS/CAMS point and layer variables.

## Layer Export

The GFS point package is generated from the local Open-Meteo API, not from the
old Python GRIB parsing chain. GFS WebP layers are then rendered from that same
point package so the point API and map layers use the same Open-Meteo snapshot.
CAMS layers are generated from the local Open-Meteo air-quality API.
The reusable API-backed layer encoder remains available as
`scripts/build_openmeteo_layers.py`, and the production GFS path uses
`scripts/render_gfs_layers_from_point_package.py` for point/layer consistency.

Before serving or exporting products, generate the local Open-Meteo `.om`
runtime data from source files. The GFS point API uses Open-Meteo's `gfs_global`
mixer, so both `gfs013` and `gfs025` must be present locally. `gfs025` supplies
variables missing from GFS013 sflux files, including visibility and several
weather-code dependencies.

Point-output parity also requires Open-Meteo's Copernicus DEM90 static data for
land elevation correction. For production, keep the runtime data local and
preseed `copernicus_dem90/static/lat_*.om` files; use `REMOTE_DATA_DIRECTORY`
only for explicitly approved reference/debug runs because it allows HTTP range
reads from processed `.om` mirrors.

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

Build the matching point package for a known validated Open-Meteo window:

```bash
python3 scripts/build_openmeteo_point_package.py \
  --api-base-url http://127.0.0.1:18080/v1/forecast \
  --output-dir ./data/openmeteo_points/gfs013_point \
  --model gfs_global \
  --run 2026-06-26T18:00 \
  --start-hour 2026-06-25T07:00 \
  --end-hour 2026-06-27T08:00
```

Render GFS layers from that point package:

```bash
python3 scripts/render_gfs_layers_from_point_package.py \
  --point-dir ./data/openmeteo_points/gfs013_point \
  --output-dir ./data/openmeteo_layers/gfs013_surface
```

Build the server layer products used by production:

```bash
bash scripts/build_server_openmeteo_layers.sh
```

The server flow writes GFS WebP layers to
`data/openmeteo_layers/gfs013_surface` and CAMS WebP layers to
`data/openmeteo_layers/cams_global`, and writes the matching GFS point package to
`data/openmeteo_points/gfs013_point`. It defaults to 50 hourly frames from the
current UTC hour and can be pinned with `WEATHER_OPENMETEO_LAYER_START_HOUR`,
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
