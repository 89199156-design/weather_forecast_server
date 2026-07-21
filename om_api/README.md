# Singapore OM point API

The Rust API is part of `89199156-design/weather_forecast_server` and is built
from the same Git revision as `../om_webp`. Its initial implementation was
copied from the validated Shanghai service; Singapore changes are made only in
this repository.

The API reads the Swift producer root directly:

```text
/opt/1panel/apps/weather_forecast_server/data/om_producer
```

It supports the public forecast, air-quality and route endpoints. Client
requests use the currently loaded immutable snapshot and never scan producer
directories. A successful native pipeline sends one `SIGHUP`; the API builds
the replacement snapshot in the background and swaps it only after loading
succeeds.

## Build and deploy

Use the repository-level scripts on the Singapore Linux server:

```bash
bash scripts/build_native_rust_artifacts.sh
bash scripts/deploy_native_rust_artifacts.sh
```

The deployment installs an atomic systemd drop-in that maps the stable host
point-data directory to `OM_DEM_ROOT`, restarts the API, verifies the effective
environment through `/proc/<MainPID>/environ`, and calls the real forecast
endpoint without an elevation override. The default is:

```text
/opt/1panel/apps/weather_forecast_server/data/point
```

Set `WEATHER_OM_DEM_ROOT` only when the host's persistent point-data root is at
a different absolute path. The installer validates every configured DEM
latitude chunk and rolls back the drop-in if service or endpoint verification
fails. Existing deployments can apply only this contract with:

```bash
bash scripts/install_native_api_dem_root.sh
```

The deployment manifest records the exact repository commit, Rust version and
binary SHA-256 values. API and WebP binaries from different commits are not a
valid production deployment.

The OM decoder library is loaded from:

```text
/opt/1panel/apps/weather_om_api/native/libomfileformat.so
```

## Runtime contract

- GFS contains three strict `f000...f005` history runs and two complete
  `f000...f384` runs.
- A null in the newest complete GFS run may fall back only to the immediately
  previous complete run at the same valid time. If that value is also null, or
  the previous run does not reach the newest tail, the result remains null.
- CAMS main forecasts retain three complete direct-hourly ECPDS runs in the
  immutable `cams` namespace. The separate ADS greenhouse product retains three
  daily 00Z runs on its native three-hour schedule in the independent immutable
  `cams_greenhouse` namespace. Its target 00Z date comes from the locally
  published ECPDS run date; there is no fixed release-lag rule.
- Official formulas, units, precision, daily aggregation and weather-code
  semantics are preserved. China AQI, regional cropping and direct-hourly CAMS
  ingestion are documented project-specific behavior.

## Producer versus adapter diagnostics

`om-raw-point` decodes an exact source-run OM entry without interpolation,
fallback, derivation or elevation correction:

```bash
ulimit -n 65535
/opt/1panel/apps/weather_om_api/bin/om-raw-point \
  --data-root /opt/1panel/apps/weather_forecast_server/data/om_producer \
  --omfile-lib /opt/1panel/apps/weather_om_api/native/libomfileformat.so \
  --product gfs013_surface \
  --variable precipitation \
  --valid-time 2026-07-22T03:00:00Z \
  --source-run 2026071700 \
  --latitude 40 --longitude 120
```

For a parity error, compare raw OM first. Only when raw values agree should the
Rust interpolation, derivation, fallback or JSON adapter be investigated. Do
not add an API compensation for a Swift producer error.

License: AGPL-3.0-or-later.
