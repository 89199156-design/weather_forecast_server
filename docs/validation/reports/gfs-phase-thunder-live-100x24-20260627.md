# GFS Precipitation Phase and Thunderstorm Alignment - 2026-06-27

Scope:

- Singapore public point package: `/opt/1panel/apps/weather/data/point_package`
- Singapore public GFS layers: `/opt/1panel/apps/weather/data/gfs013_surface`
- Shanghai mirror point package: `/opt/1panel/apps/weather/data/point_package`
- Shanghai mirror GFS layers: `/opt/1panel/apps/weather/data/gfs013_surface`

Rules:

- `weather_code` remains the raw Open-Meteo output.
- `precip_phase_code` is derived only from precipitation weather codes:
  - `1`: rain (`51/53/55/61/63/65/80/81/82`)
  - `2`: snow (`71/73/75/77/85/86`)
  - `4`: freezing-rain risk (`56/57/66/67`)
  - `0`: no generated precipitation phase
- Thunderstorm codes (`95/96/99`) are not precipitation phase codes.
- `thunderstorm_code` remains a separate signal derived from Open-Meteo `weather_code`.

Build/update:

- Existing `weather_code` package was preserved.
- `precip_phase_code` and `thunderstorm_code` were recomputed from the existing point package.
- GFS WebP layers were re-rendered from the updated point package.
- Run remained `2026-06-26T18:00`; window remained `2026-06-26T19:00` to `2026-06-28T20:00`; frame count remained `50`.

Singapore result:

- point package `generated_at`: `1782560047`
- layer manifest `generated_at`: `1782560406`
- phase code counts: `0=10712788`, `1=4010296`, `2=47932`, `4=4734`
- thunderstorm code counts: `0=14716219`, `95=59531`
- point phase metadata now lists only `0/1/2/4`
- layer `precip_phase.range` is `[0.0, 4.0]`

Shanghai mirror:

- point package SHA256 matched Singapore for `point_weather.bin` and `point_weather_meta.json`.
- GFS layer SHA256 manifest matched Singapore for all `901` files.
- Temporary/interrupted build data and Shanghai backup directories were removed after verification.

Validation:

```text
python scripts\validate_live_gfs_chain.py --points 100 --frames 24 --timeout 300 --max-examples 20
```

Result:

- `point_checks`: `67200`
- `layer_checks`: `45600`
- `mismatch_count`: `0`
- `point_update_timestamps`: `[1782500400]`
- manifest window: `2026-06-26T19:00` to `2026-06-28T20:00`

Screenshot-point recheck:

- requested point: `lat=11.1250`, `lon=111.7247`
- nearest grid point: `lat=11.070614`, `lon=111.679688`
- hour: `2026-06-27T10:00Z`
- point API:
  - `weatherCode=95`
  - `weatherText=雷暴`
  - `precip1hMm=0.0`
  - `rain1hMm=0.0`
  - `showers1hMm=0.0`
  - `snowfallCm=0.0`
  - `precipPhaseCode=0`
  - `precipPhaseText=无明显降水`
  - `thunderstormCode=95`
- decoded layers:
  - `tp=0.0`
  - `precip_phase=0.0`
  - `thunderstorm_code=95.0`
