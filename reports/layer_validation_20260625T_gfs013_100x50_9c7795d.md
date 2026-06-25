# Open-Meteo Layer Validation

- layer_dir: `data/openmeteo_layers/gfs013_surface`
- api_base_url: `http://127.0.0.1:18080/v1/forecast`
- model: `gfs013`
- api_options: `{"wind_speed_unit": "ms"}`
- points: 100
- frames: 50
- layers: cloud_total_1, cloud_high_1, cloud_mid_1, cloud_low_1, t2m, r2, wind, tp, snod, gust, vis, prmsl
- checked_values: 65000
- mismatch_count: 0
- elapsed_seconds: 298.512
