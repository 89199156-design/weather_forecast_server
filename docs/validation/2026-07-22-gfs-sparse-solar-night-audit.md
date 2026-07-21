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

At the evening boundary, the official code assigns `ktD = ktC` immediately
when the future D solar average is below the minimum. This precedes the later
B/A recovery of a missing C. A finite C is therefore copied to D, but a NaN C
leaves D as NaN even if C is subsequently recovered. The clone instead copied
the recovered daytime C to D too late, leaking radiation past sunset. Simply
removing the copy also discarded the required finite pre-recovery C at a
different evening edge.

The repair mirrors the official assignment point and ordering. It does not
inspect the output variable, point, timestamp, or expected value. Paired
regressions cover both sides of the boundary:
`sparse_solar_full_series_keeps_official_nan_d_at_night` and
`sparse_solar_full_series_copies_finite_pre_recovery_c_to_nighttime_d`.
Both repositories' complete Rust test suites passed before deployment.

The subsequent Shanghai point-4 mismatch also showed why the full operation is
required: official solar-derived inputs were positive at
`9.899124,137.8125`, `2026-07-31T01:00Z`, while the pointwise sparse reader
returned zero. Open-Meteo processes the complete hourly array sequentially and
deaverages C in place after each interval. That processed C is then the A or B
input of the next interval. The shared Rust reader now ports the full official
operation, while Singapore's already-dense 385-frame production files bypass
the sparse path. Regression
`sparse_solar_full_series_reuses_deaveraged_values_sequentially` covers this
cross-interval dependency without encoding the failed point or expected API
value.
