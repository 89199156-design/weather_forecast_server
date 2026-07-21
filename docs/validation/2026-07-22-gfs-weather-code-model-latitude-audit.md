# GFS weather-code model-latitude parity audit (2026-07-22)

## Scope and immutable evidence

- Frozen official snapshot: `20260721_gfs2018_cams2012_full_500`
- Snapshot index SHA-256: `f86bae73910ab2f9208c221986dbae452f3451ae0210a31df00b84c9ae1748f3`
- GFS run: `2026072018`
- First strict mismatch after the CAMS repair: point 88, request coordinate
  `13.765053,123.16406`, `cell_selection=land`, at `2026-07-23T14:00Z`
- Official `weather_code`: `81`
- Former clone `weather_code`: `95`
- Open-Meteo source revision used by the frozen run audit:
  `b743cbc9a7fab3f8f7dda85968fb770eee48b9ec`
- Official implementation:
  <https://github.com/open-meteo/open-meteo/blob/b743cbc9a7fab3f8f7dda85968fb770eee48b9ec/Sources/App/Helper/WeatherCode.swift>
- Official GFS reader call site:
  <https://github.com/open-meteo/open-meteo/blob/b743cbc9a7fab3f8f7dda85968fb770eee48b9ec/Sources/App/Gfs/GfsController.swift>

## Reproduction

For the failing frame, the frozen official response and clone response agree on
all public weather-code inputs:

| Input | Value |
| --- | ---: |
| cloud cover | 100% |
| precipitation | 4.1 mm |
| showers | 4.1 mm |
| snowfall | 0 cm |
| CAPE | 930 J/kg |
| wind gust | 2 m/s |
| visibility | 3720 m |

The internal GFS values at the selected cells are lifted index `-2.6`,
convective inhibition `23`, and boundary-layer height `15 m`.

The land-selection result for the GFS013 surface grid is latitude
`13.647903`. With that model latitude, the exact Open-Meteo
`calculateThunderstormProbability` port returns approximately `59.993%`.
Because Open-Meteo tests `> 60` (not `>= 60`), it does not return thunderstorm
code 95 and subsequently returns moderate rain-shower code 81.

Using the original request latitude `13.765053` with the same inputs returns
approximately `60.058%`, which incorrectly crosses the strict threshold and
returns 95.

Production requests confirmed the coordinate defect directly: multiple input
latitudes that resolved to the same GFS013 land cell and identical input values
changed from 81 to 95 solely as the original input latitude crossed the
probability threshold.

## Root cause

The point API's time-slab optimization already replaces every variable's
coordinate with the product-specific cell selected by `cell_selection`.
However, `read_weather_code_grid_series` reconstructed the latitude from the
original request coordinate. It therefore combined selected-cell meteorology
with the wrong latitude in the official tropical thunderstorm adjustment.

The single-hour path already used the selected GFS013 model latitude. The bug
was limited to the optimized full-series path used by normal hourly requests.

## Repair

For one-point grid/time-slab calls, both weather-code grid functions now obtain
the latitude from the same request-scoped GFS013 `ModelSampling` used to read
the weather inputs. Regional multi-cell production keeps its existing explicit
grid-coordinate behavior.

The regression fixes the general coordinate contract. It does not change the
official probability formula, the `> 60` threshold, any weather-code value, or
the frozen data.

## Regression test

`point_weather_code_uses_land_selected_surface_model_latitude` fixes the
failing production values and asserts both sides of the threshold:

- selected model latitude `13.647903` -> code `81`
- original request latitude `13.765053` -> code `95`

The complete Rust test suite must pass before deployment, followed by a direct
production replay of point 88 and resumption of the strict 500-point snapshot
comparison from the failed point.
