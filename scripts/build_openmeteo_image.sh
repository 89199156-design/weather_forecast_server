#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE_NAME="${WEATHER_OPENMETEO_IMAGE:-weather-forecast-openmeteo}"
IMAGE_TAG="${WEATHER_OPENMETEO_TAG:-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)}"
CONTEXT_DIR="$REPO_ROOT"

docker build \
  --file "$REPO_ROOT/docker/openmeteo-engine.Dockerfile" \
  --tag "$IMAGE_NAME:$IMAGE_TAG" \
  --tag "$IMAGE_NAME:latest" \
  "$CONTEXT_DIR"

printf '%s\n' "$IMAGE_NAME:$IMAGE_TAG"
