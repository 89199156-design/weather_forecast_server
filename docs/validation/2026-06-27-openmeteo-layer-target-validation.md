# Open-Meteo Layer Target Validation Record

Date: 2026-06-27

## Scope

This record covers the WebP layer products generated from the selected
Open-Meteo-compatible engines:

- GFS forecast/single-runs engine: `036c1d940f2dd5af48f899c2d8162d00d12d3c49`
- CAMS air-quality engine: `acfb7eb13ffdca9d3772c57716c240d3a7d73da5`

The Open-Meteo engine source is not patched for layer semantics. Layers are
generated from Open-Meteo API output and then decoded back to compare with the
public Open-Meteo APIs.

## Build Inputs

- Local GFS source: `http://127.0.0.1:18081/v1/forecast`
- Local CAMS source: `http://127.0.0.1:18084/v1/air-quality`
- GFS run: `2026-06-26T06:00`
- Time window: 24 hourly frames, `2026-06-26T06:00` through `2026-06-27T05:00`
- Region grid: `597 x 495`, China and surrounding region

The local GFS layer build does not send the production `Host:
single-runs-api.open-meteo.com` header. That header enables the upstream
production rate limiter in local validation. A pre-build control check compared
the local no-Host request to the public `single-runs-api.open-meteo.com`
response via the Seoul server for 5 points, 14 variables, and 24 frames with
`0` mismatches.

## Validated Layers

GFS layers:

- `cloud_total_1`
- `cloud_high_1`
- `cloud_mid_1`
- `cloud_low_1`
- `t2m`
- `r2`
- `wind`
- `tp`
- `snod`
- `gust`
- `vis`
- `weather_code`
- `precip_phase`
- `thunderstorm_code`
- `prmsl`

CAMS layers:

- `pm2_5`
- `pm10`
- `carbon_monoxide`
- `nitrogen_dioxide`
- `sulphur_dioxide`
- `ozone`
- `aerosol_optical_depth`
- `dust`
- `uv_index`
- `uv_index_clear_sky`
- `us_aqi`
- `european_aqi`

## Public API Reference

- GFS reference API: `https://single-runs-api.open-meteo.com/v1/forecast`
- CAMS reference API: `https://air-quality-api.open-meteo.com/v1/air-quality`
- Reference network path: Seoul server SSH proxy

## Results

GFS layer validation:

- batches: `100 / 100`
- points: `1000 / 1000`
- frames: `24`
- checked values: `384000`
- failed batches: `0`
- report: `docs/validation/reports/layer-target-dual-c72056a-f6cc85c-20260626T0600Z/gfs/summary-100x10x24.json`

CAMS layer validation:

- batches: `100 / 100`
- points: `1000 / 1000`
- frames: `24`
- checked values: `288000`
- failed batches: `0`
- report: `docs/validation/reports/layer-target-dual-c72056a-f6cc85c-20260626T0600Z/cams/summary-100x10x24.json`

## Tooling Notes

The layer exporter request size was reduced and retry controls were added for
Open-Meteo API requests. The target validation runner now fetches public API
reference data in larger point chunks while preserving the required 100 batch
reporting shape. WebP image decoding cache size was raised to cover the full
layer/frame working set and avoid repeated decode churn.
