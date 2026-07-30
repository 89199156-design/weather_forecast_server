# Singapore Rust source provenance

Singapore and Shanghai build the Rust point API and WebP renderer from the same
default-branch revision of `89199156-design/om_weather_server`. That repository
contains both `om_api/` and `webp/om_webp/`; the latter uses the sibling API
crate as its path dependency.

The production installer refuses a dirty worktree, records the full Git commit
and SHA-256 of each installed binary, and installs both binaries from that one
revision. The deployed server checkout, GitHub default branch, source-revision
files, and binary build-info records must agree. The historical Rust snapshots
still present in this producer repository are not deployment inputs.

The `om-raw-point` diagnostic binary reads an exact stored OM entry without
interpolation, derivation or fallback. It exists to prove whether a parity
difference originates in Swift OM production or in the Rust adapter; it is not
an HTTP endpoint.
