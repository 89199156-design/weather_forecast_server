#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
DEFAULT_ENV_FILE="$APP_DIR/config/singapore.private.env"
if [[ ! -f "$DEFAULT_ENV_FILE" ]]; then
  DEFAULT_ENV_FILE="$APP_DIR/config/singapore.example.env"
fi
ENV_FILE="${WEATHER_OPENMETEO_ENV_FILE:-$DEFAULT_ENV_FILE}"

declare -A WEATHER_ENV_OVERRIDES=()

capture_weather_env_overrides() {
  local name
  while IFS='=' read -r name _; do
    if [[ "$name" == WEATHER_* ]]; then
      WEATHER_ENV_OVERRIDES["$name"]="${!name}"
    fi
  done < <(env)
}

restore_weather_env_overrides() {
  local name
  for name in "${!WEATHER_ENV_OVERRIDES[@]}"; do
    printf -v "$name" '%s' "${WEATHER_ENV_OVERRIDES[$name]}"
    export "$name"
  done
}

source_env_file() {
  local file="$1"
  # Normalize CRLF so Windows-side packaging cannot break bash source.
  source <(sed 's/\r$//' "$file")
}

is_truthy() {
  local value="${1:-}"
  value="${value,,}"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" || "$value" == "on" ]]
}

utc_hour_after() {
  local start_hour="$1"
  local offset_hours="$2"
  python3 - "$start_hour" "$offset_hours" <<'PY'
from datetime import datetime, timedelta, timezone
import sys

start = datetime.strptime(sys.argv[1], "%Y-%m-%dT%H:00").replace(tzinfo=timezone.utc)
print((start + timedelta(hours=int(sys.argv[2]))).strftime("%Y-%m-%dT%H:00"))
PY
}

capture_weather_env_overrides
if [[ -f "$ENV_FILE" ]]; then
  set -a
  source_env_file "$ENV_FILE"
  set +a
fi
restore_weather_env_overrides

LAYER_ROOT_DIR="${WEATHER_OPENMETEO_LAYER_ROOT_DIR:-$APP_DIR/data/openmeteo_layers}"
GFS_OUTPUT_DIR="${WEATHER_OPENMETEO_LAYER_DIR:-$LAYER_ROOT_DIR/gfs013_surface}"
CAMS_OUTPUT_DIR="${WEATHER_OPENMETEO_CAMS_LAYER_DIR:-$LAYER_ROOT_DIR/cams_global}"
PUBLIC_DATA_DIR="${WEATHER_OPENMETEO_PUBLIC_DATA_DIR:-$APP_DIR/data/public}"
GFS_API_URL="${WEATHER_OPENMETEO_GFS_API_URL:-http://127.0.0.1:18080/v1/forecast}"
CAMS_API_URL="${WEATHER_OPENMETEO_CAMS_API_URL:-http://127.0.0.1:18080/v1/air-quality}"
LAYER_START_HOUR="${WEATHER_OPENMETEO_LAYER_START_HOUR:-$(date -u '+%Y-%m-%dT%H:00')}"
LAYER_FRAME_COUNT="${WEATHER_OPENMETEO_LAYER_FRAME_COUNT:-121}"
LAYER_END_HOUR="${WEATHER_OPENMETEO_LAYER_END_HOUR:-$(utc_hour_after "$LAYER_START_HOUR" "$((LAYER_FRAME_COUNT - 1))")}"
LAYER_CHUNK_SIZE="${WEATHER_OPENMETEO_LAYER_CHUNK_SIZE:-250}"
LAYER_TIMEOUT="${WEATHER_OPENMETEO_LAYER_TIMEOUT:-120}"
LAYER_REQUEST_RETRIES="${WEATHER_OPENMETEO_LAYER_REQUEST_RETRIES:-2}"
LAYER_REQUEST_RETRY_DELAY="${WEATHER_OPENMETEO_LAYER_REQUEST_RETRY_DELAY:-2}"
LAYER_REQUEST_PAUSE="${WEATHER_OPENMETEO_LAYER_REQUEST_PAUSE:-0}"
GFS_MODEL="${WEATHER_OPENMETEO_LAYER_MODEL:-gfs_global}"
CAMS_DOMAIN="${WEATHER_OPENMETEO_CAMS_LAYER_DOMAIN:-cams_global}"
GFS_RUN="${WEATHER_OPENMETEO_GFS_RUN:-}"
BUILD_GFS="${WEATHER_OPENMETEO_BUILD_GFS_LAYERS:-true}"
BUILD_CAMS="${WEATHER_OPENMETEO_BUILD_CAMS_LAYERS:-true}"

if ! [[ "$LAYER_FRAME_COUNT" =~ ^[0-9]+$ ]] || [[ "$LAYER_FRAME_COUNT" -lt 1 ]]; then
  printf '%s\n' "WEATHER_OPENMETEO_LAYER_FRAME_COUNT must be a positive integer." >&2
  exit 2
fi

cd "$APP_DIR"
mkdir -p "$GFS_OUTPUT_DIR" "$CAMS_OUTPUT_DIR" "$PUBLIC_DATA_DIR"

GFS_RUN_ARGS=()
if [[ -n "$GFS_RUN" ]]; then
  GFS_RUN_ARGS=(--run "$GFS_RUN")
fi

publish_public_link() {
  local target="$1"
  local link="$2"
  local tmp_link="${link}.tmp.$$"
  rm -f "$tmp_link"
  ln -s "$target" "$tmp_link"
  if [[ -e "$link" && ! -L "$link" ]]; then
    printf 'Refusing to replace non-symlink public path: %s\n' "$link" >&2
    rm -f "$tmp_link"
    exit 3
  fi
  mv -Tf "$tmp_link" "$link"
}

if is_truthy "$BUILD_GFS"; then
  python3 scripts/build_openmeteo_layers.py \
    --scope gfs \
    --api-base-url "$GFS_API_URL" \
    --output-dir "$GFS_OUTPUT_DIR" \
    --model "$GFS_MODEL" \
    "${GFS_RUN_ARGS[@]}" \
    --start-hour "$LAYER_START_HOUR" \
    --end-hour "$LAYER_END_HOUR" \
    --chunk-size "$LAYER_CHUNK_SIZE" \
    --timeout-seconds "$LAYER_TIMEOUT" \
    --request-retries "$LAYER_REQUEST_RETRIES" \
    --request-retry-delay "$LAYER_REQUEST_RETRY_DELAY" \
    --request-pause "$LAYER_REQUEST_PAUSE"
else
  printf '%s\n' "Skipping GFS layers: WEATHER_OPENMETEO_BUILD_GFS_LAYERS is disabled."
fi

if is_truthy "$BUILD_CAMS"; then
  python3 scripts/build_openmeteo_layers.py \
    --scope cams \
    --api-base-url "$CAMS_API_URL" \
    --output-dir "$CAMS_OUTPUT_DIR" \
    --domain "$CAMS_DOMAIN" \
    --start-hour "$LAYER_START_HOUR" \
    --end-hour "$LAYER_END_HOUR" \
    --chunk-size "$LAYER_CHUNK_SIZE" \
    --timeout-seconds "$LAYER_TIMEOUT" \
    --request-retries "$LAYER_REQUEST_RETRIES" \
    --request-retry-delay "$LAYER_REQUEST_RETRY_DELAY" \
    --request-pause "$LAYER_REQUEST_PAUSE"
else
  printf '%s\n' "Skipping CAMS layers: WEATHER_OPENMETEO_BUILD_CAMS_LAYERS is disabled."
fi

publish_public_link "$GFS_OUTPUT_DIR" "$PUBLIC_DATA_DIR/gfs013_surface"
publish_public_link "$CAMS_OUTPUT_DIR" "$PUBLIC_DATA_DIR/cams_global"
