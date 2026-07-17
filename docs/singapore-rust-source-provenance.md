# Singapore Rust source provenance

The Singapore Rust API and WebP implementations live in this repository and
are deployed only from this repository.

Their initial source snapshots were copied from the already validated Shanghai
implementations:

- `om_api/`: `89199156-design/om_weather_server` commit
  `c4249dedbc902f5a05a50ef629162f2124dba528`
- `om_webp/`: `89199156-design/om_weather_webp` commit
  `7ad616b3cdbc7609c54fe439575b0583ffd6e902`

Those repositories are read-only references for Singapore. After the copy,
Singapore changes are committed only to
`89199156-design/weather_forecast_server`. WebP uses the sibling
`../om_api` path dependency, so both binaries are built and deployed from one
identical Git revision.

The `om-raw-point` diagnostic binary reads an exact stored OM entry without
interpolation, derivation or fallback. It exists to prove whether a parity
difference originates in Swift OM production or in the Rust adapter; it is not
an HTTP endpoint.
