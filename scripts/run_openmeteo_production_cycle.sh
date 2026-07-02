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

  echo "$(date '+%F %T') [OPENMETEO_PRODUCTION] download runtime data"
  bash scripts/download_openmeteo_runtime_data.sh

  echo "$(date '+%F %T') [OPENMETEO_PRODUCTION] restart local Open-Meteo API"
  bash scripts/deploy_singapore_candidate.sh

  echo "$(date '+%F %T') [OPENMETEO_PRODUCTION] build layer products"
  bash scripts/build_server_openmeteo_layers.sh

  echo "$(date '+%F %T') [OPENMETEO_PRODUCTION] completed"
} 9>"$LOCK_FILE" >> "$LOG_DIR/openmeteo_production_cycle.log" 2>&1
