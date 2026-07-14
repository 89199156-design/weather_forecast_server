#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE_NAME="${WEATHER_OPENMETEO_IMAGE:-weather-forecast-openmeteo}"
if [[ -n "${WEATHER_OPENMETEO_TAG:-}" ]]; then
  IMAGE_TAG="$WEATHER_OPENMETEO_TAG"
elif git -C "$REPO_ROOT" rev-parse HEAD >/dev/null 2>&1; then
  SOURCE_ID="$({
    git -C "$REPO_ROOT" rev-parse HEAD
    git -C "$REPO_ROOT" diff --binary -- docker/openmeteo-engine.Dockerfile vendor/open-meteo
  } | sha256sum | cut -c1-12)"
  IMAGE_TAG="native-$SOURCE_ID"
else
  IMAGE_TAG="native-$(date -u +%Y%m%d%H%M%S)"
fi
CONTEXT_DIR="$REPO_ROOT"

is_truthy() {
  local value="${1:-}"
  value="${value,,}"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" || "$value" == "on" ]]
}

BUILD_TAG_ARGS=(--tag "$IMAGE_NAME:$IMAGE_TAG")
if is_truthy "${WEATHER_OPENMETEO_TAG_LATEST:-false}"; then
  BUILD_TAG_ARGS+=(--tag "$IMAGE_NAME:latest")
fi

docker build \
  --file "$REPO_ROOT/docker/openmeteo-engine.Dockerfile" \
  "${BUILD_TAG_ARGS[@]}" \
  "$CONTEXT_DIR"

printf '%s\n' "$IMAGE_NAME:$IMAGE_TAG"
