# Layer validation failure: 9ac02e0

- Version under test: `9ac02e0 Preserve layer vmin in manifest`
- Candidate container: `weather-forecast-openmeteo:2072a5e`
- Layer output: `data/openmeteo_layers/gfs013_surface`
- Validation gate: `100 points x 50 hourly frames`
- Result: failed

## Command

```bash
python3 scripts/validate_openmeteo_layers.py \
  --layer-dir ./data/openmeteo_layers/gfs013_surface \
  --api-base-url http://127.0.0.1:18080/v1/forecast \
  --max-points 100 \
  --max-times 50 \
  --chunk-size 100 \
  --report reports/layer_validation_20260625T_gfs013_100x50_9ac02e0.md
```

## Observed problem

- Checked values: `65,000`
- Mismatches: `1`
- Failing point: `lat=33.914737`, `lon=139.921875`
- Failing time: `2026-06-27T02:00`
- Failing layer: `wind`
- Expected API values: `u=15.5`, `v=104.8`
- Decoded layer values: `u=null`, `v=null`

## Root cause

The layer manifest declared wind units as `m/s`, and the wind WebP encoding range was designed for `m/s`, but the layer export and validation API calls did not explicitly set Open-Meteo's `wind_speed_unit=ms` option. Open-Meteo therefore returned the default wind component unit, `km/h`. At this edge point, the default API returned `v=104.8 km/h`; the exporter treated it as an out-of-range component and encoded the pixel as invalid.

This was an API input-unit contract bug in the layer export path, not an Open-Meteo engine or point-interpolation mismatch.

## Fix direction

Make the layer export API input explicit with `wind_speed_unit=ms`, store that option in the layer manifest, and make layer validation replay the same manifest API options before rerunning validation from the failed `100x50` gate.
