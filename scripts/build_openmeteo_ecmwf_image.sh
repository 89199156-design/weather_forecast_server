#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
UPSTREAM_DIR="$REPO_ROOT/vendor/open-meteo-ecmwf"
PATCH_PATH="$REPO_ROOT/vendor/patches/open-meteo-ecmwf-regional.patch"
DOCKERFILE="$REPO_ROOT/docker/openmeteo-ecmwf.Dockerfile"
IMAGE_NAME="${WEATHER_ECMWF_OPENMETEO_IMAGE:-weather-forecast-ecmwf}"
EXPECTED_UPSTREAM="b743cbc9a7fab3f8f7dda85968fb770eee48b9ec"

[[ -f "$PATCH_PATH" ]] || { printf '%s\n' "Missing ECMWF source patch: $PATCH_PATH" >&2; exit 1; }
[[ -f "$DOCKERFILE" ]] || { printf '%s\n' "Missing ECMWF Dockerfile: $DOCKERFILE" >&2; exit 1; }
[[ -d "$UPSTREAM_DIR/.git" || -f "$UPSTREAM_DIR/.git" ]] \
  || { printf '%s\n' "Initialise vendor/open-meteo-ecmwf submodule first" >&2; exit 1; }

ACTUAL_UPSTREAM="$(git -C "$UPSTREAM_DIR" rev-parse HEAD)"
[[ "$ACTUAL_UPSTREAM" == "$EXPECTED_UPSTREAM" ]] \
  || { printf '%s\n' "Unexpected ECMWF Open-Meteo source: $ACTUAL_UPSTREAM" >&2; exit 1; }
git -C "$UPSTREAM_DIR" diff --quiet
git -C "$UPSTREAM_DIR" diff --cached --quiet
git -C "$UPSTREAM_DIR" apply --check "$PATCH_PATH"

PATCH_SHA256="$(sha256sum "$PATCH_PATH" | awk '{print $1}')"
SOURCE_ID="$({
  printf '%s\n' "$ACTUAL_UPSTREAM"
  printf '%s\n' "$PATCH_SHA256"
  sha256sum "$DOCKERFILE" | awk '{print $1}'
} | sha256sum | cut -c1-12)"
IMAGE_TAG="${WEATHER_ECMWF_OPENMETEO_TAG:-ifs025-$SOURCE_ID}"

docker build \
  --file "$DOCKERFILE" \
  --build-arg "OPENMETEO_UPSTREAM_COMMIT=$ACTUAL_UPSTREAM" \
  --build-arg "ECMWF_PATCH_SHA256=$PATCH_SHA256" \
  --build-arg "ECMWF_SOURCE_ID=$SOURCE_ID" \
  --tag "$IMAGE_NAME:$IMAGE_TAG" \
  "$REPO_ROOT"

printf '%s\n' "$IMAGE_NAME:$IMAGE_TAG"
