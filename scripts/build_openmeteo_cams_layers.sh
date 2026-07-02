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

LAYER_ROOT_DIR="${WEATHER_OPENMETEO_LAYER_ROOT_DIR:-$APP_DIR/data/openmeteo_layers}"
CAMS_OUTPUT_DIR="${WEATHER_OPENMETEO_CAMS_LAYER_DIR:-$LAYER_ROOT_DIR/cams_global}"
PUBLIC_DATA_DIR="${WEATHER_OPENMETEO_PUBLIC_DATA_DIR:-$APP_DIR/data/public}"
LAYER_START_HOUR="${WEATHER_OPENMETEO_LAYER_START_HOUR:-$(date -u '+%Y-%m-%dT%H:00')}"
LAYER_FRAME_COUNT="${WEATHER_OPENMETEO_LAYER_FRAME_COUNT:-121}"
LAYER_END_HOUR="${WEATHER_OPENMETEO_LAYER_END_HOUR:-$(utc_hour_after "$LAYER_START_HOUR" "$((LAYER_FRAME_COUNT - 1))")}"
LAYER_CHUNK_SIZE="${WEATHER_OPENMETEO_LAYER_CHUNK_SIZE:-250}"
CAMS_DOMAIN="${WEATHER_OPENMETEO_CAMS_LAYER_DOMAIN:-cams_global}"

cd "$APP_DIR"
mkdir -p "$CAMS_OUTPUT_DIR" "$PUBLIC_DATA_DIR"

EXPORT_DIR_HOST="$DATA_DIR/layer_export_tmp/cams_$$"
EXPORT_DIR_CONTAINER="/app/data/layer_export_tmp/cams_$$"
REQUEST_HOST="$EXPORT_DIR_HOST/request.json"
REQUEST_CONTAINER="$EXPORT_DIR_CONTAINER/request.json"
mkdir -p "$EXPORT_DIR_HOST"
if [[ "$(id -u)" -eq 0 ]]; then
  chown "$OPENMETEO_UID:$OPENMETEO_GID" "$EXPORT_DIR_HOST"
fi

cleanup() {
  rm -f "${SANITIZED_ENV_FILE:-}"
  if [[ -n "${EXPORT_DIR_HOST:-}" ]]; then
    cleanup_download_work_dirs "$EXPORT_DIR_HOST"
  fi
}
trap cleanup EXIT

python3 scripts/build_openmeteo_layers.py \
  --scope cams \
  --prepare-export-request "$REQUEST_HOST" \
  --domain "$CAMS_DOMAIN" \
  --start-hour "$LAYER_START_HOUR" \
  --end-hour "$LAYER_END_HOUR" \
  --chunk-size "$LAYER_CHUNK_SIZE"

run_openmeteo export-layer-grid \
  --request "$REQUEST_CONTAINER" \
  --output-dir "$EXPORT_DIR_CONTAINER"

python3 scripts/build_openmeteo_layers.py \
  --scope cams \
  --export-dir "$EXPORT_DIR_HOST" \
  --output-dir "$CAMS_OUTPUT_DIR" \
  --domain "$CAMS_DOMAIN" \
  --start-hour "$LAYER_START_HOUR" \
  --end-hour "$LAYER_END_HOUR" \
  --chunk-size "$LAYER_CHUNK_SIZE"

mkdir -p "$PUBLIC_DATA_DIR/openmeteo_layers"
cp -f "$APP_DIR/config/weather_layer_catalog.json" "$PUBLIC_DATA_DIR/openmeteo_layers/weather_layer_catalog.json"
publish_public_link "$CAMS_OUTPUT_DIR" "$PUBLIC_DATA_DIR/openmeteo_layers/cams_global"
