#!/usr/bin/env bash
set -euo pipefail

RUN="${1:-${WEATHER_CAMS_RUN:-}}"
if [[ -z "$RUN" ]]; then
  printf '%s\n' "Usage: run_cams_ads_production_cycle.sh YYYYMMDDHH" >&2
  exit 2
fi

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_CAMS_ADS_LOCK_FILE:-/tmp/weather_openmeteo_cams_ads_cycle.lock}"

run_to_utc_layer_start() {
  python3 - "$1" <<'PY'
from datetime import datetime, timezone
import sys

run = datetime.strptime(sys.argv[1], "%Y%m%d%H").replace(tzinfo=timezone.utc)
print(run.strftime("%Y-%m-%dT%H:00"))
PY
}

run_to_greenhouse_run() {
  python3 - "$1" <<'PY'
from datetime import datetime, timezone
import sys

run = datetime.strptime(sys.argv[1], "%Y%m%d%H").replace(tzinfo=timezone.utc)
print(run.replace(hour=0).strftime("%Y%m%d%H"))
PY
}

mkdir -p "$LOG_DIR"

{
  flock -n 9 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] previous job still running, skip."
    exit 0
  }

  cd "$APP_DIR"
  if [[ "${WEATHER_GIT_PULL:-false}" == "true" ]]; then
    git pull --ff-only
  fi

  export WEATHER_CAMS_RUN="$RUN"
  export WEATHER_OPENMETEO_LAYER_START_HOUR="$(run_to_utc_layer_start "$RUN")"
  export WEATHER_OPENMETEO_LAYER_FRAME_COUNT="121"
  unset WEATHER_OPENMETEO_LAYER_END_HOUR
  export WEATHER_OPENMETEO_PUBLIC_DATA_DIR="${WEATHER_OPENMETEO_PUBLIC_DATA_DIR:-/opt/1panel/apps/weather/data}"
  export WEATHER_OPENMETEO_LAYER_ROOT_DIR="${WEATHER_OPENMETEO_LAYER_ROOT_DIR:-$APP_DIR/data/openmeteo_layers}"
  DATA_DIR="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}"
  CAMS_GREENHOUSE_RUN="${WEATHER_CAMS_GREENHOUSE_RUN:-$(run_to_greenhouse_run "$RUN")}"
  export WEATHER_CAMS_GREENHOUSE_RUN="$CAMS_GREENHOUSE_RUN"

  cleanup_cams_generated_products() {
    rm -rf \
      "$DATA_DIR/cams_global" \
      "$DATA_DIR/data_run/cams_global" \
      "$DATA_DIR/cams_global_greenhouse_gases" \
      "$DATA_DIR/data_run/cams_global_greenhouse_gases"
  }

  cleanup_cams_generated_products
  download_start="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$download_start [OPENMETEO_CAMS_ADS] download runtime data run=$RUN start=$download_start"
  bash scripts/download_openmeteo_cams_ads_data.sh
  download_end="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$download_end [OPENMETEO_CAMS_ADS] download runtime data run=$RUN end=$download_end"

  python3 scripts/validate_openmeteo_latest_run.py \
    --data-dir "${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}" \
    --run "$RUN" \
    --domains cams_global \
    --min-frames 121
  python3 scripts/validate_openmeteo_latest_run.py \
    --data-dir "${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}" \
    --run "$CAMS_GREENHOUSE_RUN" \
    --domains cams_global_greenhouse_gases \
    --min-frames 41

  layer_start="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$layer_start [OPENMETEO_CAMS_ADS] build CAMS layer products start=$layer_start"
  bash scripts/build_openmeteo_cams_layers.sh
  layer_end="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$layer_end [OPENMETEO_CAMS_ADS] build CAMS layer products end=$layer_end"

  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] completed run=$RUN"
} 9>"$LOCK_FILE" >> "$LOG_DIR/openmeteo_cams_ads_cycle.log" 2>&1
