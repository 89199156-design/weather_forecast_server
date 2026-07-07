#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"

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

load_weather_env
openmeteo_set_runtime_defaults
require_dem_source
write_sanitized_env_file

LAYER_ROOT_DIR="${WEATHER_OPENMETEO_LAYER_ROOT_DIR:-$APP_DIR/data/webp}"
CAMS_OUTPUT_DIR="${WEATHER_OPENMETEO_CAMS_LAYER_DIR:-$LAYER_ROOT_DIR/cams_global}"
PUBLIC_DATA_DIR="${WEATHER_OPENMETEO_PUBLIC_DATA_DIR:-$APP_DIR/data/public}"
CAMS_API_URL="${WEATHER_OPENMETEO_CAMS_API_URL:-http://127.0.0.1:18080/v1/air-quality}"
LAYER_START_HOUR="${WEATHER_OPENMETEO_LAYER_START_HOUR:-$(date -u '+%Y-%m-%dT%H:00')}"
LAYER_FRAME_COUNT="${WEATHER_OPENMETEO_LAYER_FRAME_COUNT:-121}"
LAYER_END_HOUR="${WEATHER_OPENMETEO_LAYER_END_HOUR:-$(utc_hour_after "$LAYER_START_HOUR" "$((LAYER_FRAME_COUNT - 1))")}"
LAYER_CHUNK_SIZE="${WEATHER_OPENMETEO_LAYER_CHUNK_SIZE:-250}"
LAYER_TIMEOUT="${WEATHER_OPENMETEO_LAYER_TIMEOUT:-120}"
LAYER_REQUEST_RETRIES="${WEATHER_OPENMETEO_LAYER_REQUEST_RETRIES:-2}"
LAYER_REQUEST_RETRY_DELAY="${WEATHER_OPENMETEO_LAYER_REQUEST_RETRY_DELAY:-2}"
LAYER_REQUEST_PAUSE="${WEATHER_OPENMETEO_LAYER_REQUEST_PAUSE:-0}"
CAMS_DOMAIN="${WEATHER_OPENMETEO_CAMS_LAYER_DOMAIN:-cams_global}"

cd "$APP_DIR"
mkdir -p "$CAMS_OUTPUT_DIR" "$PUBLIC_DATA_DIR"

cleanup() {
  rm -f "${SANITIZED_ENV_FILE:-}"
}
trap cleanup EXIT

bash scripts/run_openmeteo_api_server.sh

python3 scripts/build_webp.py \
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

mkdir -p "$PUBLIC_DATA_DIR/webp"
cp -f "$APP_DIR/config/weather_layer_catalog.json" "$PUBLIC_DATA_DIR/webp/weather_layer_catalog.json"
publish_public_link "$CAMS_OUTPUT_DIR" "$PUBLIC_DATA_DIR/webp/cams_global"
