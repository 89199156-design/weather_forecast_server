#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_GFS_PROBE_LOCK_FILE:-/tmp/weather_openmeteo_gfs_probe.lock}"
CYCLE_LOCK_FILE="${WEATHER_OPENMETEO_GFS_LOCK_FILE:-/tmp/weather_openmeteo_gfs_cycle.lock}"

mkdir -p "$LOG_DIR"

{
  flock -n 9 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] previous probe or GFS cycle still running, skip."
    exit 0
  }

  {
    flock -n 8 || {
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] GFS production cycle already running, skip probe."
      exit 0
    }
  } 8>"$CYCLE_LOCK_FILE"

  cd "$APP_DIR"
  if [[ "${WEATHER_GIT_PULL:-false}" == "true" ]]; then
    git pull --ff-only
  fi

  data_dir="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}"
  max_hour="${WEATHER_GFS_MAX_FORECAST_HOUR:-120}"
  probe_output=""
  if ! probe_output="$(python3 scripts/probe_gfs_official_run.py --data-dir "$data_dir" --max-forecast-hour "$max_hour" 2>&1)"; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] $probe_output"
    exit 0
  fi

  set -- $probe_output
  if [[ "${1:-}" != "READY" || -z "${2:-}" ]]; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] $probe_output"
    exit 0
  fi

  run="$2"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] complete official run=$run"
  WEATHER_GFS_RUN="$run" bash scripts/run_gfs_production_cycle.sh "$run"
} 9>"$LOCK_FILE" >> "$LOG_DIR/openmeteo_gfs_probe.log" 2>&1

