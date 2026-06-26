# Weather Forecast Server

This repository is the public AGPL weather forecast server for the Singapore
weather-model node.

The migration target is to use the Open-Meteo engine directly instead of
maintaining a Python reimplementation of Open-Meteo weather logic. Our local
code is limited to:

- China and surrounding-region domain selection.
- GFS download source configuration and regional slicing for a lightweight
  server.
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
2. Add our regional GFS download/source configuration.
3. Export point API packages from Open-Meteo-derived data.
4. Export layers from the same Open-Meteo-derived data.
5. Deploy to Singapore and remove old satellite code/tasks there.
6. Validate 50, then 100, then 500 points x 50 forecast frames for point API
   and layer consistency.

## Layer Export

Layer products are generated from the local Open-Meteo API, not from the old
Python GRIB parsing chain. The Python layer code only requests point values
from Open-Meteo and encodes those values into the existing WebP/manifest
product shape used by clients.

Before serving or exporting products, download the Open-Meteo runtime data. The
GFS point API uses Open-Meteo's `gfs_global` mixer, so both `gfs013` and `gfs025`
must be present locally. `gfs025` supplies variables missing from GFS013 sflux
files, including visibility and several weather-code dependencies.

Point-output parity also requires Open-Meteo's Copernicus DEM90 static data for
land elevation correction. For lightweight nodes, configure
`WEATHER_DEM_REMOTE_DATA_DIRECTORY` to an owned mirror with the Open-Meteo
`data/copernicus_dem90/static/lat_*.om` layout instead of preloading the full
DEM locally.

```bash
bash scripts/download_openmeteo_runtime_data.sh
```

Write the source-derived GFS/CAMS API inventory:

```bash
python3 scripts/openmeteo_api_inventory.py \
  --output docs/validation/openmeteo-api-inventory.json
```

Run the required point validation gates. The runner stops on the first failed
gate, so a failed 50-point gate prevents 100/500-point validation:

```bash
python3 scripts/run_openmeteo_validation_gates.py \
  --api-base-url http://127.0.0.1:18080 \
  --reference-base-url http://127.0.0.1:18081 \
  --output-dir docs/validation/reports
```

Build layers for a known validated window:

```bash
python3 scripts/build_openmeteo_layers.py \
  --api-base-url http://127.0.0.1:18080/v1/forecast \
  --output-dir ./data/openmeteo_layers/gfs013_surface \
  --model gfs_global \
  --start-hour 2026-06-25T07:00 \
  --end-hour 2026-06-27T08:00
```

Validate generated layers against the same Open-Meteo API before promotion:

```bash
python3 scripts/validate_openmeteo_layers.py \
  --layer-dir ./data/openmeteo_layers/gfs013_surface \
  --api-base-url http://127.0.0.1:18080/v1/forecast \
  --max-points 50 \
  --max-times 50
```

Run the validation gates in order: 50 points x 50 frames, then 100 x 50, then
500 x 50. Stop on the first mismatch and record the changed revision, report,
and source-chain analysis before changing code again.
