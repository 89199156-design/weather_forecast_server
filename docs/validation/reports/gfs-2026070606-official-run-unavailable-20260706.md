# GFS 2026070606 Official API Validation Hold

- date: 2026-07-06
- deployed commit: `182f4f6`
- local production run: `2026-07-06T06:00:00Z`
- official reference endpoint: `https://single-runs-api.open-meteo.com/v1/forecast`

Current local GFS WebP-vs-OM grid validation passed:

- report: `docs/validation/reports/layers-gfs-2026070606-182f4f6-2000x121-localom-20260706.md`
- scope: GFS WebP layers against local Open-Meteo `.om`
- checked: `2000` grid points x `121` frames x `18` layers = `4,598,000` values
- result: `0 mismatch`

Official API validation for this same GFS run is currently blocked because the
official single-runs API does not expose `2026-07-06T06:00Z` for
`ncep_gfs025`.

Observed from Seoul reference host:

```text
2026-07-06T12 HTTP 400 The requested model run is not available. Model: ncep_gfs025, run: 2026-07-06T12:00Z
2026-07-06T06 HTTP 400 The requested model run is not available. Model: ncep_gfs025, run: 2026-07-06T06:00Z
2026-07-06T00 OK
2026-07-05T18 OK
2026-07-05T12 OK
2026-07-05T06 OK
2026-07-05T00 OK
2026-07-04T18 OK
2026-07-04T12 OK
```

Do not treat GFS official parity for `2026070606` as complete until the
official API exposes the same run, or a later locally retained run overlaps an
officially available run.
