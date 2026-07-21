# CAMS Official Source-Cadence Repair — 2026-07-21

## Frozen evidence

- Official snapshot run: `cams_global` `2026072012`
- Snapshot index SHA-256:
  `f86bae73910ab2f9208c221986dbae452f3451ae0210a31df00b84c9ae1748f3`
- First strict Singapore difference:
  point `6.853241,132.65625`, `nitrogen_dioxide`, forecast hour `59`;
  official `0.0`, Singapore `0.1`.
- The corresponding native three-hour frames at forecast hours
  `54,57,60,63` are `0.1,0.1,0.0,0.0` in both systems.

The authenticated ECPDS source values for the same cell were independently
decoded and unit-scaled. Directly ingesting forecast hour 59 gives `0.05436`,
which serializes as `0.1`. Applying Open-Meteo's Hermite interpolation to the
official three-hour source cadence gives `0.02963`, which serializes as `0.0`.

## Root cause and repair

ECPDS exposes hourly files for the model-level CAMS products, but the public
Open-Meteo CAMS database uses those products at three-hour source cadence and
generates the public hourly series with the upstream Hermite interpolation.
The local downloader had intentionally removed the upstream `hour % 3` filter,
so values that are interpolation frames in the official database were stored
as direct hourly source frames locally.

The repair restores the upstream cadence contract:

- surface CAMS variables remain direct hourly source files;
- model-level variables use forecast hours divisible by three;
- the standard Open-Meteo conversion fills all 121 public hourly frames;
- the remote completeness probe checks the same per-variable source cadence;
- a guarded current-run repair mode rebuilds all three retained ECPDS runs in
  clean staging and publishes a new immutable coverage revision.

This is a production input-contract correction, not a comparison exception,
API response override, batch freeze, or test-only value substitution.
