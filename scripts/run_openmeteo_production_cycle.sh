#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_PRODUCTION_LOCK_FILE:-/tmp/weather_openmeteo_production_cycle.lock}"

mkdir -p "$LOG_DIR"

{
  flock -n 9 || {
    echo "$(date '+%F %T') [OPENMETEO_PRODUCTION] previous job still running, skip."
    exit 0
  }

  cd "$APP_DIR"
  if [[ "${WEATHER_GIT_PULL:-false}" == "true" ]]; then
    git pull --ff-only
  fi

  download_start="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$download_start [OPENMETEO_PRODUCTION] download runtime data start=$download_start"
  bash scripts/download_openmeteo_runtime_data.sh
  download_end="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$download_end [OPENMETEO_PRODUCTION] download runtime data end=$download_end"

  layer_start="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$layer_start [OPENMETEO_PRODUCTION] build layer products start=$layer_start"
  bash scripts/build_server_openmeteo_layers.sh
  layer_end="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$layer_end [OPENMETEO_PRODUCTION] build layer products end=$layer_end"

  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_PRODUCTION] completed"
} 9>"$LOCK_FILE" >> "$LOG_DIR/openmeteo_production_cycle.log" 2>&1
