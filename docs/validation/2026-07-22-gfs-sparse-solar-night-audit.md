# GFS sparse solar night-boundary audit (2026-07-22)

- Frozen snapshot SHA-256:
  `f86bae73910ab2f9208c221986dbae452f3451ae0210a31df00b84c9ae1748f3`
- GFS run: `2026072018`
- Point: `50.31566,107.92969`, nearest cell, elevation NaN
- Time: `2026-08-04T13:00Z`
- Official and dense native result: `sunshine_duration=0`, `uv_index=0`
- Former sparse range-bundle result: `sunshine_duration=151.75`,
  `uv_index=0.05`
- Official source revision: `b743cbc9a7fab3f8f7dda85968fb770eee48b9ec`
- Official sparse implementation:
  <https://github.com/open-meteo/open-meteo/blob/b743cbc9a7fab3f8f7dda85968fb770eee48b9ec/Sources/App/Helper/InterpolationInplace.swift>

After f120, GFS solar variables contain missing hourly frames between stored
three-hour frames. Open-Meteo fills them with
`interpolateInplaceSolarBackwards`.

At the evening boundary, the future D clearness factor is unavailable because
its solar factor is below the minimum. The official code deliberately leaves
`ktD` as NaN. Hermite arithmetic therefore remains NaN, and Swift's
`max(0, NaN)` yields zero for the missing nighttime hour.

The clone incorrectly replaced NaN D with the preceding daytime C clearness
factor. This produced positive shortwave/UV radiation after sunset, which then
produced a nonzero sunshine duration.

The repair removes the non-official D fallback. It does not inspect the output
variable, point, timestamp, or expected value. Regression
`sparse_solar_interpolation_keeps_official_nan_d_at_night` returned `3.5`
before the repair and exactly `0` afterward. Both repositories' complete Rust
test suites passed before deployment.
