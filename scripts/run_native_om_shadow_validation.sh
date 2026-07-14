#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"
SCOPE="${1:-gfs}"
if [[ "$SCOPE" != "gfs" && "$SCOPE" != "cams" ]]; then
  printf '%s\n' "Usage: run_native_om_shadow_validation.sh [gfs|cams]" >&2
  exit 2
fi
PRODUCTION_CONTAINER="${WEATHER_OPENMETEO_CONTAINER:-weather-forecast-openmeteo-api}"
SHADOW_CONTAINER="${WEATHER_OPENMETEO_SHADOW_CONTAINER:-weather-forecast-openmeteo-shadow-validation}"
SHADOW_PORT="${WEATHER_OPENMETEO_SHADOW_PORT:-18081}"
REPORT_PATH="${WEATHER_OPENMETEO_SHADOW_REPORT:-$PRODUCER_ROOT/reports/${SCOPE}_shadow_validation.json}"

if [[ ! "$SHADOW_PORT" =~ ^[0-9]+$ ]] || [[ "$SHADOW_PORT" -lt 1024 || "$SHADOW_PORT" -gt 65535 ]]; then
  printf '%s\n' "WEATHER_OPENMETEO_SHADOW_PORT must be an unprivileged TCP port." >&2
  exit 2
fi

CURRENT_LINK="$PRODUCER_ROOT/current/$SCOPE"
if [[ ! -L "$CURRENT_LINK" ]]; then
  printf '%s\n' "Missing native $SCOPE current symlink: $CURRENT_LINK" >&2
  exit 2
fi
COVERAGE_DIR="$(readlink -f -- "$CURRENT_LINK")"
COVERAGES_ROOT="$(readlink -f -- "$PRODUCER_ROOT/coverages/$SCOPE")"
if [[ -z "$COVERAGE_DIR" || -z "$COVERAGES_ROOT" || "$(dirname -- "$COVERAGE_DIR")" != "$COVERAGES_ROOT" ]]; then
  printf '%s\n' "Current $SCOPE coverage resolves outside $PRODUCER_ROOT/coverages/$SCOPE" >&2
  exit 2
fi
if [[ ! -f "$COVERAGE_DIR/coverage.json" ]]; then
  printf '%s\n' "Coverage has no manifest: $COVERAGE_DIR" >&2
  exit 2
fi

if docker container inspect "$SHADOW_CONTAINER" >/dev/null 2>&1; then
  printf '%s\n' "Refusing to replace existing container: $SHADOW_CONTAINER" >&2
  exit 2
fi

IMAGE_REF="${WEATHER_OPENMETEO_SHADOW_IMAGE:-}"
if [[ -z "$IMAGE_REF" ]]; then
  IMAGE_REF="$(docker container inspect --format '{{.Config.Image}}' "$PRODUCTION_CONTAINER" 2>/dev/null || true)"
fi
if [[ -z "$IMAGE_REF" ]]; then
  printf '%s\n' "Set WEATHER_OPENMETEO_SHADOW_IMAGE or start the production API container first." >&2
  exit 2
fi

mkdir -p "$(dirname -- "$REPORT_PATH")"
CONTAINER_ID=""
cleanup() {
  if [[ -n "$CONTAINER_ID" ]]; then
    local actual_id
    actual_id="$(docker container inspect --format '{{.Id}}' "$SHADOW_CONTAINER" 2>/dev/null || true)"
    if [[ "$actual_id" == "$CONTAINER_ID" ]]; then
      docker stop --time 10 "$SHADOW_CONTAINER" >/dev/null
    fi
  fi
}
trap cleanup EXIT INT TERM

CONTAINER_ID="$(
  docker run -d --rm \
    --name "$SHADOW_CONTAINER" \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
    --mount "type=bind,src=$COVERAGE_DIR,dst=/app/data,readonly" \
    --publish "127.0.0.1:$SHADOW_PORT:8080" \
    "$IMAGE_REF"
)"

ready=false
for _ in $(seq 1 30); do
  if ! docker container inspect --format '{{.State.Running}}' "$SHADOW_CONTAINER" 2>/dev/null | grep -qx true; then
    docker logs --tail 100 "$SHADOW_CONTAINER" >&2 || true
    printf '%s\n' "Shadow Open-Meteo API stopped before becoming ready." >&2
    exit 1
  fi
  if curl --silent --show-error --max-time 2 \
    "http://127.0.0.1:$SHADOW_PORT/" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  docker logs --tail 100 "$SHADOW_CONTAINER" >&2 || true
  printf '%s\n' "Shadow Open-Meteo API did not become ready within 30 seconds." >&2
  exit 1
fi

VALIDATOR="$APP_DIR/scripts/validate_native_om_coverage.py"
if [[ "$SCOPE" == "cams" ]]; then
  VALIDATOR="$APP_DIR/scripts/validate_native_cams_coverage.py"
fi
python3 "$VALIDATOR" \
  --producer-root "$PRODUCER_ROOT" \
  --api-base-url "http://127.0.0.1:$SHADOW_PORT" \
  --output-report "$REPORT_PATH"
