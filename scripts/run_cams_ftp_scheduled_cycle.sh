#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_CAMS_FTP_SCHEDULE_LOCK_FILE:-/tmp/weather_openmeteo_cams_ftp_schedule.lock}"
CYCLE_LOCK_FILE="${WEATHER_OPENMETEO_CAMS_FTP_LOCK_FILE:-/tmp/weather_openmeteo_cams_ftp_cycle.lock}"
GLOBAL_LOCK_FILE="${WEATHER_OPENMETEO_GLOBAL_LOCK_FILE:-/tmp/weather_openmeteo_production.lock}"

mkdir -p "$LOG_DIR"

(
trap 'task_rc=$?; trap - EXIT; printf "\036WEATHER_TASK_RC=%s\n" "$task_rc"; exit "$task_rc"' EXIT
{
  flock -n 9 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP_SCHEDULE] previous schedule check still running, skip."
    exit 0
  }

  cd "$APP_DIR"
  if [[ "${WEATHER_GIT_PULL:-false}" == "true" ]]; then
    git pull --ff-only
  fi

  {
    flock -n 7 || {
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP_SCHEDULE] another Open-Meteo production cycle is running, skip probe."
      exit 0
    }
  } 7>"$GLOBAL_LOCK_FILE"

  data_dir="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"
  {
    flock -n 8 || {
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP_SCHEDULE] CAMS FTP/ECPDS production cycle already running, skip probe."
      exit 0
    }
  } 8>"$CYCLE_LOCK_FILE"

  probe_output=""
  probe_rc=0
  probe_output="$(python3 scripts/probe_cams_ftp_run.py --data-dir "$data_dir" 2>&1)" || probe_rc=$?
  ready_line="$(printf '%s\n' "$probe_output" | awk '$1 == "READY" && $2 != "" { line = $0 } END { print line }')"
  if [[ -z "$ready_line" ]]; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP_SCHEDULE] $probe_output"
    exit 0
  fi
  read -r ready_marker run ready_reference_time <<< "$ready_line"
  if [[ "$ready_marker" != "READY" || -z "$run" ]]; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP_SCHEDULE] invalid probe output rc=$probe_rc: $probe_output"
    exit 0
  fi

  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_FTP_SCHEDULE] start run=$run reference_time=$ready_reference_time"
  WEATHER_CAMS_RUN="$run" bash scripts/run_native_model_pipeline.sh cams "$run"
} 9>"$LOCK_FILE"
) 2>&1 | python3 "$APP_DIR/scripts/task_progress_reporter.py" \
  --task "CAMS 生产更新" \
  --watch-root "${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}/staging" \
  --log-file "$LOG_DIR/openmeteo_cams_ftp_schedule.log"
