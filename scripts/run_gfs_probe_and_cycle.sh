#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
TASK_NAME="weather_gfs_probe_cycle"
PANEL_DB="/opt/1panel/db/1Panel.db"
CURRENT_LOG_PATH="$(readlink -f "/proc/$$/fd/1")"
TASK_STATE="$(/usr/bin/python3 "$APP_DIR/scripts/check_1panel_v1_task_state.py" \
  --database "$PANEL_DB" \
  --current-task "$TASK_NAME" \
  --current-log-path "$CURRENT_LOG_PATH")"
case "$TASK_STATE" in
  run\|*) ;;
  skip\|*) printf '%s\n' "跳过｜任务：$TASK_NAME｜原因：${TASK_STATE#skip|}"; exit 0 ;;
  *) printf '%s\n' "失败｜任务：$TASK_NAME｜原因：未知任务状态 $TASK_STATE" >&2; exit 2 ;;
esac
export WEATHER_1PANEL_VERIFIED_TASK="$TASK_NAME"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env
if [[ "${WEATHER_1PANEL_VERIFIED_TASK:-}" != "$TASK_NAME" ]]; then
  printf '%s\n' "拒绝执行：GFS 生产入口的 1Panel 身份在加载配置时被改变" >&2
  exit 2
fi
readonly WEATHER_1PANEL_VERIFIED_TASK
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
TASK_SCOPE="gfs"
PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"

mkdir -p "$LOG_DIR"

(
finish_task() {
  main_rc=$?
  cleanup_rc=0
  trap - EXIT
  set +e
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] stage=cleanup after task rc=$main_rc"
  if container_state="$(openmeteo_task_container_state "$TASK_SCOPE")"; then
    if [[ "$container_state" == "running" ]]; then
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] preserve running task container and staging during final cleanup"
    elif cleanup_openmeteo_task_container "$TASK_SCOPE"; then
      python3 "$APP_DIR/scripts/cleanup_native_task_staging.py" \
        --producer-root "$PRODUCER_ROOT" \
        --scope gfs || cleanup_rc=$?
    else
      cleanup_rc=$?
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] preserve staging because container cleanup failed rc=$cleanup_rc" >&2
    fi
  else
    cleanup_rc=$?
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] preserve container and staging because state inspection failed rc=$cleanup_rc" >&2
  fi
  final_rc=$main_rc
  if (( cleanup_rc != 0 )); then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] final cleanup failed rc=$cleanup_rc" >&2
    if (( final_rc == 0 )); then
      final_rc=$cleanup_rc
    fi
  fi
  printf "\036WEATHER_TASK_RC=%s\n" "$final_rc"
  exit "$final_rc"
}
trap finish_task EXIT
{
  export WEATHER_OPENMETEO_TASK_SCOPE="$TASK_SCOPE"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] stage=startup cleanup previous abnormal task residue"
  container_state="$(openmeteo_task_container_state "$TASK_SCOPE")"
  if [[ "$container_state" == "running" ]]; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] detached task container is still running; keep it and skip duplicate trigger"
    printf "\036WEATHER_TASK_SKIP=%s\n" "检测到本任务遗留的运行中容器"
    exit 0
  fi
  cleanup_openmeteo_task_container "$TASK_SCOPE"
  python3 "$APP_DIR/scripts/cleanup_native_task_staging.py" \
    --producer-root "$PRODUCER_ROOT" \
    --scope gfs
  export WEATHER_TASK_CLEANUP_DONE=true

  cd "$APP_DIR"
  if [[ "${WEATHER_GIT_PULL:-false}" == "true" ]]; then
    git pull --ff-only
  fi

  data_dir="$PRODUCER_ROOT"
  python3 scripts/reconcile_native_current_pointer.py \
    --producer-root "$data_dir" \
    --group gfs
  applied_state="$(python3 scripts/pending_native_api_coverage.py \
    --producer-root "$data_dir" \
    --group gfs)"
  if [[ "$applied_state" == PENDING\ * ]]; then
    read -r pending_marker run pending_coverage <<<"$applied_state"
    printf "\036WEATHER_TASK_TARGET_RUN=%s\n" "$run"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] retry unpublished API snapshot run=$run coverage=$pending_coverage"
    bash scripts/run_native_model_pipeline.sh gfs "$run" apply-published
    exit 0
  fi
  max_hour="${WEATHER_GFS_MAX_FORECAST_HOUR:-384}"
  probe_output=""
  if ! probe_output="$(python3 scripts/probe_gfs_official_run.py --data-dir "$data_dir" --max-forecast-hour "$max_hour" 2>&1)"; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] $probe_output"
    printf "\036WEATHER_TASK_SKIP=%s\n" "官方尚无可发布的新完整 GFS 批次"
    exit 0
  fi

  ready_line="$(printf '%s\n' "$probe_output" | awk '$1 == "READY" && $2 != "" { print; exit }')"
  if [[ -z "$ready_line" ]]; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] $probe_output"
    printf "\036WEATHER_TASK_SKIP=%s\n" "官方尚无可发布的新完整 GFS 批次"
    exit 0
  fi

  set -- $ready_line
  run="$2"
  printf "\036WEATHER_TASK_TARGET_RUN=%s\n" "$run"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_PROBE] complete official run=$run"
  WEATHER_GFS_RUN="$run" bash scripts/run_native_model_pipeline.sh gfs "$run"
}
) 2>&1 | python3 "$APP_DIR/scripts/task_progress_reporter.py" \
  --task "GFS 生产更新" \
  --default-stage "启动前清理" \
  --watch-root "$PRODUCER_ROOT/staging" \
  --log-file "$LOG_DIR/openmeteo_gfs_probe.log"
