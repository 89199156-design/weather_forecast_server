# Layer validation failure: 3b6e7c1

- Version under test: `3b6e7c1 Add Open-Meteo API backed layer export`
- Candidate container: `weather-forecast-openmeteo:2072a5e`
- Layer output: `data/openmeteo_layers/gfs013_surface`
- Validation gate: `50 points x 50 hourly frames`
- Result: failed

## Command

```bash
python3 scripts/validate_openmeteo_layers.py \
  --layer-dir ./data/openmeteo_layers/gfs013_surface \
  --api-base-url http://127.0.0.1:18080/v1/forecast \
  --max-points 50 \
  --max-times 50 \
  --chunk-size 50 \
  --report reports/layer_validation_20260625T_gfs013_50x50_3b6e7c1.md
```

## Observed problem

- Checked values: `32,500`
- Mismatches: `2,500`
- First mismatch pattern: `t2m` expected about `28.1 C`, decoded layer value about `128.1 C`
- Other checked layers in the first gate did not show the same offset pattern.

## Root cause

The scalar WebP encoder wrote temperature values with `vmin=-100.0`, but the layer manifest did not publish `vmin`. The validator and downstream consumers therefore decoded scalar pixels with the default `vmin=0.0`. For `t2m`, this made every decoded temperature exactly `100 C` too high.

This was a layer product metadata bug, not an Open-Meteo point API or interpolation-engine mismatch. The point API validation had already passed `500 points x 50 hourly frames` on the same candidate engine.

## Fix direction

Publish `LayerDefinition.vmin` in every layer manifest, regenerate the layer product, and restart validation from the `50 points x 50 hourly frames` gate before running the `100x50` and `500x50` gates.
