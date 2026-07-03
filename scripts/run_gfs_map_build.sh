#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_BUILD_LOCK_FILE:-/tmp/weather_openmeteo_products_build.lock}"

mkdir -p "$LOG_DIR"

{
  flock -n 9 || {
    echo "$(date '+%F %T') [OPENMETEO_PRODUCTS] previous job still running, skip."
    exit 0
  }

  cd "$APP_DIR"
  if [[ "${WEATHER_GIT_PULL:-false}" == "true" ]]; then
    git pull --ff-only
  fi

  export WEATHER_OPENMETEO_PUBLIC_DATA_DIR="${WEATHER_OPENMETEO_PUBLIC_DATA_DIR:-/opt/1panel/apps/weather/data}"
  export WEATHER_OPENMETEO_LAYER_ROOT_DIR="${WEATHER_OPENMETEO_LAYER_ROOT_DIR:-$APP_DIR/data/openmeteo_layers}"

  bash scripts/build_openmeteo_gfs_layers.sh
} 9>"$LOCK_FILE" >> "$LOG_DIR/openmeteo_products_build.log" 2>&1
