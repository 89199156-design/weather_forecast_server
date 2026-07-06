#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_CAMS_FTP_SCHEDULE_LOCK_FILE:-/tmp/weather_openmeteo_cams_ftp_schedule.lock}"
CYCLE_LOCK_FILE="${WEATHER_OPENMETEO_CAMS_FTP_LOCK_FILE:-/tmp/weather_openmeteo_cams_ftp_cycle.lock}"
GLOBAL_LOCK_FILE="${WEATHER_OPENMETEO_GLOBAL_LOCK_FILE:-/tmp/weather_openmeteo_production.lock}"

mkdir -p "$LOG_DIR"

{
  flock -n 9 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP_SCHEDULE] previous schedule check still running, skip."
    exit 0
  }

  cd "$APP_DIR"
  source scripts/openmeteo_runtime_common.sh
  load_weather_env

  if [[ "${WEATHER_GIT_PULL:-false}" == "true" ]]; then
    git pull --ff-only
  fi

  {
    flock -n 7 || {
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP_SCHEDULE] another Open-Meteo production cycle is running, skip probe."
      exit 0
    }
  } 7>"$GLOBAL_LOCK_FILE"

  data_dir="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}"
  {
    flock -n 8 || {
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP_SCHEDULE] CAMS FTP/ECPDS production cycle already running, skip probe."
      exit 0
    }
  } 8>"$CYCLE_LOCK_FILE"

  probe_output=""
  if ! probe_output="$(python3 scripts/probe_cams_ftp_run.py --data-dir "$data_dir" 2>&1)"; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP_SCHEDULE] $probe_output"
    exit 0
  fi

  set -- $probe_output
  if [[ "${1:-}" != "READY" || -z "${2:-}" ]]; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP_SCHEDULE] $probe_output"
    exit 0
  fi
  run="$2"

  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP_SCHEDULE] start run=$run"
  WEATHER_CAMS_RUN="$run" bash scripts/run_cams_ftp_production_cycle.sh "$run"
} 9>"$LOCK_FILE" >> "$LOG_DIR/openmeteo_cams_ftp_schedule.log" 2>&1
