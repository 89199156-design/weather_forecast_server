#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_DIR="${WEATHER_RUST_TARGET_DIR:-$APP_ROOT/.build/rust-target}"
OUTPUT_DIR="${1:-$APP_ROOT/.build/native-rust-artifacts}"
BUILD_LOCK="${WEATHER_RUST_BUILD_LOCK_FILE:-/tmp/weather_native_rust_build.lock}"
SOURCE_REMOTE="${WEATHER_RUST_SOURCE_REMOTE:-origin}"
SOURCE_BRANCH="${WEATHER_RUST_SOURCE_BRANCH:-main}"
EXPECTED_REMOTE_URL="${WEATHER_RUST_EXPECTED_REMOTE_URL:-https://github.com/89199156-design/weather_forecast_server.git}"
EXPECTED_RUSTC_VERSION="${WEATHER_RUST_EXPECTED_RUSTC_VERSION:-1.97.1}"

API_ARTIFACTS=(om-api om-raw-point)
WEBP_ARTIFACTS=(om-webp om-grid-verify om-webp-api-verify om-webp-inspect)
ALL_ARTIFACTS=("${API_ARTIFACTS[@]}" "${WEBP_ARTIFACTS[@]}")

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "$command_name is required" >&2
    exit 1
  fi
}

require_clean_main() {
  local status branch remote_url revision remote_revision
  status="$(git status --porcelain --untracked-files=all)"
  if [[ -n "$status" ]]; then
    echo "refusing to build deployment artifacts from a dirty worktree" >&2
    printf '%s\n' "$status" >&2
    exit 1
  fi

  branch="$(git symbolic-ref --quiet --short HEAD || true)"
  if [[ "$branch" != "$SOURCE_BRANCH" ]]; then
    echo "refusing to build from branch '$branch'; expected '$SOURCE_BRANCH'" >&2
    exit 1
  fi

  remote_url="$(git remote get-url "$SOURCE_REMOTE")"
  if [[ "$remote_url" != "$EXPECTED_REMOTE_URL" ]]; then
    echo "refusing to build from unexpected remote URL: $remote_url" >&2
    echo "expected remote URL: $EXPECTED_REMOTE_URL" >&2
    exit 1
  fi

  git fetch --quiet --no-tags "$SOURCE_REMOTE" \
    "+refs/heads/$SOURCE_BRANCH:refs/remotes/$SOURCE_REMOTE/$SOURCE_BRANCH"
  revision="$(git rev-parse HEAD)"
  remote_revision="$(git rev-parse "refs/remotes/$SOURCE_REMOTE/$SOURCE_BRANCH")"
  if [[ "$revision" != "$remote_revision" ]]; then
    echo "refusing to build: HEAD $revision does not equal $SOURCE_REMOTE/$SOURCE_BRANCH $remote_revision" >&2
    exit 1
  fi
}

for command_name in cargo rustc git python3 sha256sum install flock; do
  require_command "$command_name"
done

cd "$APP_ROOT"
require_clean_main

rustc_version="$(rustc --version)"
if [[ "$rustc_version" != "rustc $EXPECTED_RUSTC_VERSION "* ]]; then
  echo "refusing to build with $rustc_version; expected rustc $EXPECTED_RUSTC_VERSION" >&2
  exit 1
fi

revision="$(git rev-parse HEAD)"
remote_url="$(git remote get-url "$SOURCE_REMOTE")"
mkdir -p "$(dirname "$BUILD_LOCK")" "$TARGET_DIR" "$OUTPUT_DIR"

{
  flock -n 8 || {
    echo "another native Rust build is already running" >&2
    exit 1
  }

  export CARGO_TARGET_DIR="$TARGET_DIR"
  export CARGO_BUILD_JOBS="${WEATHER_RUST_BUILD_JOBS:-1}"
  cargo fmt --manifest-path "$APP_ROOT/om_api/Cargo.toml" --all -- --check
  cargo fmt --manifest-path "$APP_ROOT/om_webp/Cargo.toml" --all -- --check
  cargo test --locked --manifest-path "$APP_ROOT/om_api/Cargo.toml" --all-targets
  cargo test --locked --manifest-path "$APP_ROOT/om_webp/Cargo.toml" --all-targets
  cargo build --locked --release --manifest-path "$APP_ROOT/om_api/Cargo.toml" \
    --bin om-api --bin om-raw-point
  cargo build --locked --release --manifest-path "$APP_ROOT/om_webp/Cargo.toml" \
    --bin om-webp --bin om-grid-verify --bin om-webp-api-verify --bin om-webp-inspect

  require_clean_main
  post_build_revision="$(git rev-parse HEAD)"
  if [[ "$post_build_revision" != "$revision" ]]; then
    echo "source revision changed during the Rust build" >&2
    exit 1
  fi

  for artifact in "${ALL_ARTIFACTS[@]}"; do
    rm -f "$OUTPUT_DIR/$artifact"
    install -m 0755 "$TARGET_DIR/release/$artifact" "$OUTPUT_DIR/$artifact"
  done
  rm -f "$OUTPUT_DIR/build.json"

  manifest_args=(
    "$OUTPUT_DIR/build.json"
    "$revision"
    "$SOURCE_REMOTE"
    "$SOURCE_BRANCH"
    "$remote_url"
    "$(rustc -Vv)"
    "$(cargo --version)"
  )
  for artifact in "${ALL_ARTIFACTS[@]}"; do
    manifest_args+=("$artifact" "$(sha256sum "$OUTPUT_DIR/$artifact" | awk '{print $1}')")
  done

  python3 - "${manifest_args[@]}" <<'PY'
import json
import pathlib
import sys

(
    path,
    revision,
    source_remote,
    source_branch,
    remote_url,
    rustc_version,
    cargo_version,
    *artifact_pairs,
) = sys.argv[1:]
if len(artifact_pairs) % 2:
    raise SystemExit("artifact manifest arguments must be name/hash pairs")
artifacts = {
    artifact_pairs[index]: {"sha256": artifact_pairs[index + 1]}
    for index in range(0, len(artifact_pairs), 2)
}
payload = {
    "repository": "89199156-design/weather_forecast_server",
    "revision": revision,
    "source_remote": source_remote,
    "source_branch": source_branch,
    "remote_url": remote_url,
    "rustc": rustc_version,
    "cargo": cargo_version,
    "artifacts": artifacts,
}
pathlib.Path(path).write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
} 8>"$BUILD_LOCK"

cat "$OUTPUT_DIR/build.json"
