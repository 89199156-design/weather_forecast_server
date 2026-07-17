# GFS raw OM producer diagnosis (2026-07-18)

Run identity: `2026071700`.

- Shanghai reference: official Open-Meteo bucket snapshot downloaded without
  API derivation or interpolation.
- Singapore candidate: Swift-produced native OM coverage
  `gfs_native_2026071700_surface-complete-v1`.
- Reader: `om-raw-point`, which decodes the exact stored source-run entry and
  bypasses API interpolation, fallback, derivation, elevation correction and
  JSON rounding.

At 40N, 120E the f120 values agree, while f123 exposes the producer error:

| Variable | Forecast hour | Shanghai raw OM | Singapore raw OM before fix |
| --- | ---: | ---: | ---: |
| precipitation | 120 | 0.7 | 0.7 |
| showers | 120 | 0.6 | 0.6 |
| precipitation | 123 | 9.4 | 3.1 |
| showers | 123 | 9.2 | 3.1 |

At 30N, 110E, f123 precipitation and showers are `0.4` in Shanghai and `0.1`
in the old Singapore OM (subject to each source's OM quantisation).

The boundary matches upstream Open-Meteo commit
`6059e2bd7e009b765caadd6a619002af3fd9ee21`: f120 follows f119 and represents
one hour, while f123 follows f120 and represents three hours. The old importer
multiplied precipitation rate by 3,600 seconds at every step; the official fix
uses 10,800 seconds after the sparse schedule begins. This is a Swift OM
producer defect, not a Rust API adapter defect.

Acceptance after rebuilding both retained full runs:

1. Re-run this raw gate for f120 and f123 at non-null points.
2. Require equality within the stored OM quantisation.
3. Only after the raw gate passes, compare f121/f122/f123 API output to isolate
   interpolation or adapter errors.
