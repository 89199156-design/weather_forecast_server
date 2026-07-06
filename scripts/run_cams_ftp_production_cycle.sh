#!/usr/bin/env bash
set -euo pipefail

RUN="${1:-${WEATHER_CAMS_RUN:-}}"
if [[ -z "$RUN" ]]; then
  printf '%s\n' "Usage: run_cams_ftp_production_cycle.sh YYYYMMDDHH" >&2
  exit 2
fi

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_CAMS_FTP_LOCK_FILE:-/tmp/weather_openmeteo_cams_ftp_cycle.lock}"
GLOBAL_LOCK_FILE="${WEATHER_OPENMETEO_GLOBAL_LOCK_FILE:-/tmp/weather_openmeteo_production.lock}"

run_to_utc_layer_start() {
  python3 - "$1" <<'PY'
from datetime import datetime, timezone
import sys

run = datetime.strptime(sys.argv[1], "%Y%m%d%H").replace(tzinfo=timezone.utc)
print(run.strftime("%Y-%m-%dT%H:00"))
PY
}

mkdir -p "$LOG_DIR"

{
  flock -n 9 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP] previous job still running, skip."
    exit 0
  }

  exec 8>"$GLOBAL_LOCK_FILE"
  flock -n 8 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP] another Open-Meteo production cycle is running, skip."
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

  download_start="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$download_start [OPENMETEO_CAMS_FTP] download runtime data run=$RUN start=$download_start"
  bash scripts/download_openmeteo_cams_data.sh
  download_end="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$download_end [OPENMETEO_CAMS_FTP] download runtime data run=$RUN end=$download_end"

  python3 scripts/validate_openmeteo_latest_run.py \
    --data-dir "${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}" \
    --run "$RUN" \
    --domains cams_global \
    --min-frames 121

  layer_start="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$layer_start [OPENMETEO_CAMS_FTP] build CAMS layer products start=$layer_start"
  bash scripts/build_openmeteo_cams_layers.sh
  layer_end="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$layer_end [OPENMETEO_CAMS_FTP] build CAMS layer products end=$layer_end"

  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP] completed run=$RUN"
} 9>"$LOCK_FILE" >> "$LOG_DIR/openmeteo_cams_ftp_cycle.log" 2>&1
