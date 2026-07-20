#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_GFS_PROBE_LOCK_FILE:-/tmp/weather_openmeteo_gfs_probe.lock}"
CYCLE_LOCK_FILE="${WEATHER_OPENMETEO_GFS_LOCK_FILE:-/tmp/weather_openmeteo_gfs_cycle.lock}"

mkdir -p "$LOG_DIR"

(
trap 'task_rc=$?; trap - EXIT; printf "\036WEATHER_TASK_RC=%s\n" "$task_rc"; exit "$task_rc"' EXIT
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

  export WEATHER_OPENMETEO_TASK_SCOPE=gfs
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] stage=cleanup previous abnormal task residue"
  container_state="$(openmeteo_task_container_state "$WEATHER_OPENMETEO_TASK_SCOPE")"
  if [[ "$container_state" == "running" ]]; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] detached task container is still running; keep it and skip duplicate trigger"
    exit 0
  fi
  cleanup_openmeteo_task_container "$WEATHER_OPENMETEO_TASK_SCOPE"
  python3 "$APP_DIR/scripts/cleanup_native_task_staging.py" \
    --producer-root "${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}" \
    --scope gfs
  export WEATHER_TASK_CLEANUP_DONE=true

  cd "$APP_DIR"
  if [[ "${WEATHER_GIT_PULL:-false}" == "true" ]]; then
    git pull --ff-only
  fi

  data_dir="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"
  python3 scripts/reconcile_native_current_pointer.py \
    --producer-root "$data_dir" \
    --group gfs
  applied_state="$(python3 scripts/pending_native_api_coverage.py \
    --producer-root "$data_dir" \
    --group gfs)"
  if [[ "$applied_state" == PENDING\ * ]]; then
    read -r pending_marker run pending_coverage <<<"$applied_state"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] retry unpublished API snapshot run=$run coverage=$pending_coverage"
    bash scripts/run_native_model_pipeline.sh gfs "$run" apply-published
    exit 0
  fi
  max_hour="${WEATHER_GFS_MAX_FORECAST_HOUR:-384}"
  probe_output=""
  if ! probe_output="$(python3 scripts/probe_gfs_official_run.py --data-dir "$data_dir" --max-forecast-hour "$max_hour" 2>&1)"; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] $probe_output"
    exit 0
  fi

  ready_line="$(printf '%s\n' "$probe_output" | awk '$1 == "READY" && $2 != "" { print; exit }')"
  if [[ -z "$ready_line" ]]; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] $probe_output"
    exit 0
  fi

  set -- $ready_line
  run="$2"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] complete official run=$run"
  WEATHER_GFS_RUN="$run" bash scripts/run_native_model_pipeline.sh gfs "$run"
} 9>"$LOCK_FILE"
) 2>&1 | python3 "$APP_DIR/scripts/task_progress_reporter.py" \
  --task "GFS 生产更新" \
  --default-stage "清理异常残留" \
  --watch-root "${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}/staging" \
  --log-file "$LOG_DIR/openmeteo_gfs_probe.log"
