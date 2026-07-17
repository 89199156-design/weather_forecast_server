# OM WebP Renderer

Rust renderer for the Singapore native-model pipeline. It reads the locally
published OM coverage directly through the same `om-api` snapshot,
interpolation, product-mixing and weather-code code paths used by the point API.
It never calls the HTTP API.

The production inventory matches Singapore: 18 GFS layers and 4 CAMS layers.
Both products render exactly 121 WebP files per variable at one-hour intervals,
from latest run hour 0 through hour 120. The longer five-run GFS / three-run
CAMS OM windows are used by the database and API, not by WebP. Images use
lossless RGBA WebP with the published scalar/vector encoding contract.

Each source `release_id` is built under `data/staging`. A complete immutable
release is moved to `data/releases`, then the public product symlink is switched
atomically. Existing public data remains available while a new release builds.
If the source release changes during a build, staging is discarded and nothing
is published.

## Runtime

`read_variable_grid` decodes each required regional OM rectangle in one native
call. It then applies the same interpolation, derived-variable formulas and JSON
output precision as the point API. Frames are processed with a bounded Rayon
pool; production defaults to one worker so the client-facing API keeps CPU
headroom. `OM_WEBP_WORKERS` can override the limit; `--workers 0` deliberately
uses every available CPU and is reserved for isolated/offline rendering.

```bash
/opt/1panel/apps/weather_om_webp/bin/om-webp \
  --scope gfs \
  --data-root /opt/1panel/apps/weather_forecast_server/data/om_producer \
  --output-root /opt/1panel/apps/weather_om_webp/data \
  --public-root /opt/1panel/apps/weather/data \
  --decoder-lib /opt/1panel/apps/weather_om_api/native/libomfileformat.so \
  --workers 1
```

Production does not install a standalone WebP cron job or completion watcher.
The GFS/CAMS source-cycle process calls the renderer immediately after OM
publication and validation, then signals the API exactly once. If a release was
already rendered, the renderer still validates and repairs its public symlink
before returning without decoding the OM snapshot.

## Verification

```bash
/opt/1panel/apps/weather_om_webp/bin/om-grid-verify \
  --scope gfs \
  --data-root /opt/1panel/apps/weather_forecast_server/data/om_producer \
  --decoder-lib /opt/1panel/apps/weather_om_api/native/libomfileformat.so \
  --time 2026-07-12T06:00:00Z \
  --samples 64
```

Both crates live in this repository and use `AGPL-3.0-or-later`; the renderer
directly links the API query implementation, so corresponding source must be
published together when the service is distributed or offered over a network.
