# CAMS frozen API / official source audit — 2026-07-22

## Scope

This audit covers the first remaining strict Singapore mismatch after the GFS
and CAMS production repairs:

- frozen snapshot SHA-256:
  `f86bae73910ab2f9208c221986dbae452f3451ae0210a31df00b84c9ae1748f3`
- CAMS run: `2026072012`
- point: `16.22519,101.953125`, `cell_selection=nearest`
- frame: `2026-07-25T01:00Z` (global forecast hour 109)
- variable: `carbon_monoxide`
- frozen official API: `218.0`
- Singapore API before repair: `218.5`

The purpose is to derive the value from pinned official Open-Meteo source and
immutable official source artifacts. No point/time override or comparison
exception is used.

## Pinned official source

The last public Open-Meteo commit before the `2026072012` run started was
[`b743cbc9a7fab3f8f7dda85968fb770eee48b9ec`](https://github.com/open-meteo/open-meteo/commit/b743cbc9a7fab3f8f7dda85968fb770eee48b9ec),
committed at `2026-07-20T10:33:40Z`. The regional producer baseline is
`4efb9c49fb4a3718ed385fb22580d2e0fc56bdb2`.

The official comparison between those commits contains no CAMS cadence,
Hermite, reader mixer, or JSON precision change. Both versions pin
`om-file-format` revision
`71f422b2706d8a81f1cecf52ae3073990de1ddbe`.

The pinned official source establishes the following path:

1. [`CamsDownload.swift`](https://github.com/open-meteo/open-meteo/blob/b743cbc9a7fab3f8f7dda85968fb770eee48b9ec/Sources/App/Cams/CamsDownload.swift#L136-L142)
   downloads model-level CAMS global variables only when `hour % 3 == 0`.
2. [`GenericVariableHandle.swift`](https://github.com/open-meteo/open-meteo/blob/b743cbc9a7fab3f8f7dda85968fb770eee48b9ec/Sources/App/Helper/Writer/GenericVariableHandle.swift#L409-L417)
   fills the hourly global time-series database with the variable's Hermite
   interpolation.
3. [`GenericReader.swift`](https://github.com/open-meteo/open-meteo/blob/b743cbc9a7fab3f8f7dda85968fb770eee48b9ec/Sources/App/Helper/Reader/GenericReader.swift#L197-L217)
   expands a 3-hour greenhouse reader to requested hourly timestamps.
4. [`Interpolation.swift`](https://github.com/open-meteo/open-meteo/blob/b743cbc9a7fab3f8f7dda85968fb770eee48b9ec/Sources/App/Helper/Interpolation.swift#L154-L174)
   applies the Catmull-Rom/Hermite polynomial. If C or D is unavailable, it
   replaces that lookahead value with B before evaluating and then rounds to
   the variable scale factor.
5. [`GenericReaderMixerRaw.swift`](https://github.com/open-meteo/open-meteo/blob/b743cbc9a7fab3f8f7dda85968fb770eee48b9ec/Sources/App/Helper/Reader/GenericReaderMixerRaw.swift#L114-L134)
   performs the three-frame smooth transition when the greenhouse forecast
   ends.
6. [`JsonWriter.swift`](https://github.com/open-meteo/open-meteo/blob/b743cbc9a7fab3f8f7dda85968fb770eee48b9ec/Sources/App/Helper/Writer/JsonWriter.swift#L151-L169)
   formats carbon monoxide with one decimal; it does not turn `218.5` into
   `218.0`.

## Immutable official artifacts

### CAMS global `2026072012`

The public Open-Meteo per-run file was decoded directly from:

`https://openmeteo.s3.us-west-2.amazonaws.com/data_run/cams_global/2026/07/20/1200Z/carbon_monoxide.om`

- size: `6,927,256` bytes
- SHA-256: `61c3adca2df94c616c5f7c5df4efe133e5a8897427136e67f372ca0304df0a47`
- dimensions: `[451,900,41]`
- chunks: `[1,24,41]`
- scale factor: `1`
- selected full-grid cell: `y=266,x=705`
- forecast-hour 105/108/111/114 values: `333,340,202,168`

The official Float Hermite formula gives and scale-1 rounding stores:

- forecast hour 109: `300.8888854980469` -> `301`
- forecast hour 110: `245.66665649414062` -> `246`

The regional production rebuild independently wrote both the native full-run
representation and the ordinary Open-Meteo time-series representation with
the same values:

- native full-run SHA-256:
  `f014fa4180ff85b2c3d0234b5c8564b4c73b5f08f1d93a81ef2a7a954040b50d`
- ordinary time-series `chunk_2284.om` SHA-256:
  `7a541ca919c6815a4f096f3005528de5f544d72272c14a10557ca777eac1cb48`
- ordinary time-series values around the frame: `340,301,246,202`

### CAMS greenhouse gases `2026072000`

The public Open-Meteo per-run file was decoded directly from:

`https://openmeteo.s3.us-west-2.amazonaws.com/data_run/cams_global_greenhouse_gases/2026/07/20/0000Z/carbon_monoxide.om`

- size: `79,079,304` bytes
- SHA-256: `9ac5022df3a5e3d6ec473556713aafbf65f36451e4302d71ec9bdb82c7186f3a`
- dimensions: `[1801,3600,41]`
- chunks: `[1,24,41]`
- scale factor: `1`
- selected cell: `y=1062,x=2820`
- forecast-hour 117/120 values: `145,136`

The last stored greenhouse frame is B=`136` at `2026-07-25T00:00Z`.
For the next two requested hourly frames, the official reader sees C and D as
unavailable and substitutes B for both. With A=`145`, B=C=D=`136`, the
official Hermite polynomial gives:

- `2026-07-25T01:00Z`, fraction `1/3`: `135.333...` -> scale-1 `135`
- `2026-07-25T02:00Z`, fraction `2/3`: `135.666...` -> scale-1 `136`

These two boundary extrapolation values are temporary interpolation results;
the source itself still ends at `2026-07-25T00:00Z`.

## Exact frozen API reconstruction

Iterating the official mixer backwards from the first greenhouse NaN produces
the three transition weights. The four frozen frames reconstruct exactly:

- `00:00`: `(340 + 3*136) / 4 = 187.0`
- `01:00`: `(2*301 + 2*135) / 4 = 218.0`
- `02:00`: `(3*246 + 136) / 4 = 218.5`
- `03:00`: greenhouse is unavailable, so global `202.0`

The frozen official sequence is `[187.0, 218.0, 218.5, 202.0]`.

## Root cause and repair

The regional API previously returned the last greenhouse value B unchanged for
the two hourly timestamps immediately after the final stored 3-hour frame. It
therefore used `136` instead of the official `135` at `01:00`, producing:

`(2*301 + 2*136) / 4 = 218.5`

The repair applies the official Hermite tail rule for timestamps strictly
between the last source frame and the next source cadence boundary. It uses the
previous A sample, substitutes B for unavailable C and D, applies the same
Float polynomial, rounds to the scale factor, and then runs the unchanged
official three-frame mixer. This is a general boundary rule, not a value,
point, time, or batch override.

The regression test
`cams_carbon_monoxide_hermite_extrapolates_greenhouse_tail_before_mixing`
constructs the two official source sequences and verifies the complete frozen
transition `[187.0, 218.0, 218.5, 202.0]`.

## Conclusion

The mismatch was a regional API interpolation-boundary bug: greenhouse Hermite
padding after the last stored 3-hour frame was clamped instead of evaluated.
Pinned official source and both immutable official per-run files fully explain
the frozen official `218.0`; no hard alignment or comparison exception is
required.
