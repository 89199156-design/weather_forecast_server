#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env

LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_CAMS_ADS_SCHEDULE_LOCK_FILE:-/tmp/weather_openmeteo_cams_ads_schedule.lock}"
PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"
MAX_FORECAST_HOUR="${WEATHER_CAMS_GREENHOUSE_MAX_FORECAST_HOUR:-120}"
KEEP_COVERAGES="${WEATHER_OM_CAMS_GREENHOUSE_KEEP_COVERAGES:-1}"
COVERAGE_REVISION="${WEATHER_CAMS_GREENHOUSE_COVERAGE_REVISION:-independent-v1}"
CAMS_STORAGE_LEFT_LON="${WEATHER_CAMS_STORAGE_LEFT_LON:-69}"
CAMS_STORAGE_RIGHT_LON="${WEATHER_CAMS_STORAGE_RIGHT_LON:-141}"
CAMS_STORAGE_BOTTOM_LAT="${WEATHER_CAMS_STORAGE_BOTTOM_LAT:--1}"
CAMS_STORAGE_TOP_LAT="${WEATHER_CAMS_STORAGE_TOP_LAT:-59}"
FORCE_REBUILD_CURRENT="${WEATHER_CAMS_GREENHOUSE_FORCE_REBUILD_CURRENT:-false}"

export WEATHER_REGION_LEFT_LON="$CAMS_STORAGE_LEFT_LON"
export WEATHER_REGION_RIGHT_LON="$CAMS_STORAGE_RIGHT_LON"
export WEATHER_REGION_BOTTOM_LAT="$CAMS_STORAGE_BOTTOM_LAT"
export WEATHER_REGION_TOP_LAT="$CAMS_STORAGE_TOP_LAT"

mkdir -p "$LOG_DIR" "$PRODUCER_ROOT/ads_staging" "$PRODUCER_ROOT/staging"

(
publish_dir=""
on_task_exit() {
  task_rc=$?
  if [[ -n "${publish_dir:-}" && "$publish_dir" == "$PRODUCER_ROOT/staging/"* ]]; then
    rm -rf -- "$publish_dir"
  fi
  trap - EXIT
  printf "\036WEATHER_TASK_RC=%s\n" "$task_rc"
  exit "$task_rc"
}
trap on_task_exit EXIT
{
  # This is the only ADS lock. GFS and ECPDS use different lock files and
  # different immutable publication namespaces, so all three may run at once.
  flock -n 9 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] ADS request already queued, running, downloading, or publishing; skip duplicate trigger."
    exit 0
  }

  export WEATHER_OPENMETEO_TASK_SCOPE=cams-ads
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] stage=cleanup previous abnormal task residue"
  container_state="$(openmeteo_task_container_state "$WEATHER_OPENMETEO_TASK_SCOPE")"
  if [[ "$container_state" == "running" ]]; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] detached ADS request is still queued/running; keep it and skip duplicate submission"
    exit 0
  fi
  cleanup_openmeteo_task_container "$WEATHER_OPENMETEO_TASK_SCOPE"
  python3 "$APP_DIR/scripts/cleanup_native_task_staging.py" \
    --producer-root "$PRODUCER_ROOT" \
    --scope cams_ads_publish

  cd "$APP_DIR"
  if [[ "${WEATHER_GIT_PULL:-false}" == "true" ]]; then
    git pull --ff-only
  fi

  python3 scripts/reconcile_native_current_pointer.py \
    --producer-root "$PRODUCER_ROOT" \
    --group cams_greenhouse

  applied_state="$(python3 scripts/pending_native_api_coverage.py \
    --producer-root "$PRODUCER_ROOT" \
    --group cams_greenhouse)"
  if [[ "$applied_state" == PENDING\ * ]]; then
    read -r pending_marker pending_run pending_coverage <<<"$applied_state"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] retry API apply only run=$pending_run coverage=$pending_coverage"
    bash scripts/reload_native_api_snapshot.sh cams_greenhouse "$pending_coverage"
    resumed_dir="$PRODUCER_ROOT/ads_staging/cams_ads_$pending_run"
    if [[ -d "$resumed_dir" && ! -L "$resumed_dir" ]]; then
      rm -rf -- "$resumed_dir"
    fi
    python3 scripts/cleanup_native_task_staging.py \
      --producer-root "$PRODUCER_ROOT" \
      --scope cams_ads
    exit 0
  fi

  force_plan_arg=()
  if is_truthy "$FORCE_REBUILD_CURRENT"; then
    force_plan_arg+=(--force-current)
  fi
  if state="$(python3 scripts/plan_cams_ads_update.py \
    --producer-root "$PRODUCER_ROOT" \
    "${force_plan_arg[@]}")"; then
    plan_rc=0
  else
    plan_rc=$?
  fi
  if (( plan_rc != 0 )); then
    if [[ "$state" == ERROR\ * ]]; then
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] $state" >&2
    else
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] planner failed rc=$plan_rc output=$state" >&2
    fi
    exit "$plan_rc"
  fi
  resume_pending_run=""
  if [[ "$state" == RESUME\ * ]]; then
    read -r ready_marker target_run source_runs resume_pending_run <<<"$state"
    main_run="persisted-request"
  elif [[ "$state" == READY\ * ]]; then
    read -r ready_marker main_run target_run source_runs <<<"$state"
  else
    if [[ "$state" == ERROR\ * ]]; then
      # An invalid/uncertain persisted request is evidence that a remote POST
      # may already exist. Preserve it and fail visibly rather than deleting
      # the state and submitting a duplicate.
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] $state" >&2
      exit 2
    fi
    # No persisted remote request exists, so old private work directories are
    # only unpublished residue and are safe to remove.
    python3 scripts/cleanup_native_task_staging.py \
      --producer-root "$PRODUCER_ROOT" \
      --scope cams_ads
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] $state"
    exit 0
  fi

  work_dir="$PRODUCER_ROOT/ads_staging/cams_ads_$target_run"
  python3 scripts/cleanup_native_task_staging.py \
    --producer-root "$PRODUCER_ROOT" \
    --scope cams_ads \
    --keep-name "cams_ads_$target_run"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] local ECPDS run=$main_run requires ADS run=$target_run sources=$source_runs"
  python3 scripts/prepare_cams_ads_staging.py \
    --producer-root "$PRODUCER_ROOT" \
    --staging-dir "$work_dir"
  mkdir -p "$work_dir/.ads_jobs" "$work_dir/.full_grid_rebuild_complete"
  force_rebuild_marker="$work_dir/.force_rebuild_current"
  if is_truthy "$FORCE_REBUILD_CURRENT"; then
    : > "$force_rebuild_marker"
  elif [[ -f "$force_rebuild_marker" ]]; then
    # A resumed remote request must keep the repair semantics that created its
    # private staging directory even if the later invocation omitted the flag.
    FORCE_REBUILD_CURRENT=true
  fi
  prepare_openmeteo_staging_permissions "$work_dir"
  export WEATHER_OPENMETEO_DATA_DIR="$work_dir"

  validate_greenhouse_run() {
    local source_run="$1"
    local required_variables="${WEATHER_CAMS_GREENHOUSE_VARIABLES:-carbon_monoxide}"
    PYTHONPATH="$APP_DIR/scripts${PYTHONPATH:+:$PYTHONPATH}" python3 - \
      "$work_dir" "$source_run" "$required_variables" "$target_run" <<'PY'
from pathlib import Path
import sys

from publish_native_om_coverage import validate_run_metadata, validate_runtime_variables

staging = Path(sys.argv[1])
source_run = sys.argv[2]
required = {item.strip() for item in sys.argv[3].split(",") if item.strip()}
hours = list(range(0, 121, 3))
metadata = validate_run_metadata(
    staging,
    "cams_global_greenhouse_gases",
    source_run,
    hours,
)
missing = sorted(required - set(metadata["variables"]))
if missing:
    raise SystemExit(
        f"cams_global_greenhouse_gases run {source_run} is missing: {','.join(missing)}"
    )
validate_run_metadata(
    staging,
    "cams_global_greenhouse_gases",
    source_run,
    hours,
    {variable: len(hours) for variable in metadata["variables"]},
)
if source_run == sys.argv[4]:
    validate_runtime_variables(staging, "cams_global_greenhouse_gases", metadata["variables"])
PY
  }

  IFS=',' read -ra planned_runs <<<"$source_runs"
  ordered_runs=()
  if [[ -n "$resume_pending_run" ]]; then
    ordered_runs+=("$resume_pending_run")
  fi
  for source_run in "${planned_runs[@]}"; do
    if [[ "$source_run" != "$resume_pending_run" ]]; then
      ordered_runs+=("$source_run")
    fi
  done
  for source_run in "${ordered_runs[@]}"; do
    state_file="$work_dir/.ads_jobs/${source_run}.json"
    rebuild_complete="$work_dir/.full_grid_rebuild_complete/$source_run"
    if is_truthy "$FORCE_REBUILD_CURRENT" && \
       [[ -f "$rebuild_complete" ]] && \
       validate_greenhouse_run "$source_run" >/dev/null 2>&1; then
      rm -f -- "$state_file"
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] reuse full-grid rebuilt run=$source_run"
      continue
    fi
    if ! is_truthy "$FORCE_REBUILD_CURRENT" && \
       validate_greenhouse_run "$source_run" >/dev/null 2>&1; then
      rm -f -- "$state_file"
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] reuse validated run=$source_run"
      continue
    fi
    run_dir="$work_dir/data_run/cams_global_greenhouse_gases/${source_run:0:4}/${source_run:4:2}/${source_run:6:2}/${source_run:8:2}00Z"
    if [[ ! -f "$state_file" ]]; then
      rm -rf -- "$run_dir"
    fi
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] submit once and wait run=$source_run horizon=$MAX_FORECAST_HOUR"
    WEATHER_CAMS_GREENHOUSE_RUN="$source_run" \
    WEATHER_CDS_JOB_STATE_FILE="/app/data/.ads_jobs/${source_run}.json" \
    WEATHER_CDS_POLL_INTERVAL_SECONDS="${WEATHER_CAMS_ADS_POLL_INTERVAL_SECONDS:-60}" \
    WEATHER_CDS_JOB_TIMEOUT_HOURS="${WEATHER_CAMS_ADS_JOB_TIMEOUT_HOURS:-168}" \
      bash scripts/download_openmeteo_cams_greenhouse_data.sh
    validate_greenhouse_run "$source_run"
    if is_truthy "$FORCE_REBUILD_CURRENT"; then
      : > "$rebuild_complete"
    fi
    rm -f -- "$state_file"
  done

  latest_dir="$work_dir/data_run/cams_global_greenhouse_gases/${target_run:0:4}/${target_run:4:2}/${target_run:6:2}/${target_run:8:2}00Z"
  latest_json="$work_dir/data_run/cams_global_greenhouse_gases/latest.json"
  cp -- "$latest_dir/meta.json" "$latest_json.tmp.$$"
  mv -f -- "$latest_json.tmp.$$" "$latest_json"
  python3 scripts/prune_native_om_runs.py \
    --data-dir "$work_dir" \
    --domains cams_global_greenhouse_gases \
    --retained-runs "$source_runs"
  python3 scripts/validate_openmeteo_latest_run.py \
    --data-dir "$work_dir" \
    --run "$target_run" \
    --domains cams_global_greenhouse_gases \
    --min-frames "$((MAX_FORECAST_HOUR / 3 + 1))"

  published_latest=""
  published_marker="$PRODUCER_ROOT/groups/cams_greenhouse/current/ready_for_processing.json"
  if [[ -e "$published_marker" || -L "$published_marker" ]]; then
    published_state="$(python3 scripts/validate_native_cams_greenhouse_coverage.py \
      --producer-root "$PRODUCER_ROOT")"
    published_latest="$(python3 -c \
      'import json,sys; print(json.load(sys.stdin)["latest_complete_run"])' \
      <<<"$published_state")"
  fi
  discard_without_publish=false
  if [[ -n "$published_latest" && "$published_latest" > "$target_run" ]]; then
    discard_without_publish=true
  elif [[ "$published_latest" == "$target_run" ]] && ! is_truthy "$FORCE_REBUILD_CURRENT"; then
    discard_without_publish=true
  fi
  if is_truthy "$discard_without_publish"; then
    rm -rf -- "$work_dir"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] completed persisted ADS request run=$target_run; current run=$published_latest is not older, discard private staging without downgrade"
    exit 0
  fi

  publish_dir="$PRODUCER_ROOT/staging/cams_greenhouse_${target_run}_$$"
  mkdir -p "$publish_dir/data_run"
  cp -al -- "$work_dir/cams_global_greenhouse_gases" "$publish_dir/"
  cp -al -- \
    "$work_dir/data_run/cams_global_greenhouse_gases" \
    "$publish_dir/data_run/"
  prepare_openmeteo_staging_permissions "$publish_dir"
  python3 scripts/publish_native_cams_greenhouse_coverage.py \
    --staging-dir "$publish_dir" \
    --output-root "$PRODUCER_ROOT" \
    --run "$target_run" \
    --source-runs "$source_runs" \
    --latest-max-forecast-hour "$MAX_FORECAST_HOUR" \
    --keep-coverages "$KEEP_COVERAGES" \
    --coverage-revision "$COVERAGE_REVISION" \
    --left-lon "$CAMS_STORAGE_LEFT_LON" \
    --right-lon "$CAMS_STORAGE_RIGHT_LON" \
    --bottom-lat "$CAMS_STORAGE_BOTTOM_LAT" \
    --top-lat "$CAMS_STORAGE_TOP_LAT"

  pending="$(python3 scripts/pending_native_api_coverage.py \
    --producer-root "$PRODUCER_ROOT" \
    --group cams_greenhouse)"
  if [[ "$pending" != PENDING\ * ]]; then
    echo "CAMS ADS publish did not create a pending immutable coverage" >&2
    exit 1
  fi
  read -r pending_marker published_run published_coverage <<<"$pending"
  if [[ "$published_run" != "$target_run" ]]; then
    echo "CAMS ADS published run=$published_run expected=$target_run" >&2
    exit 1
  fi
  bash scripts/reload_native_api_snapshot.sh cams_greenhouse "$published_coverage"
  python3 scripts/prune_native_coverage_history.py \
    --producer-root "$PRODUCER_ROOT" \
    --scope cams_greenhouse \
    --expected-coverage-id "$published_coverage"
  rm -rf -- "$work_dir"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] stage=cleanup retention applied and temporary ADS data removed"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS] completed run=$target_run sources=$source_runs"
} 9>"$LOCK_FILE"
) 2>&1 | python3 "$APP_DIR/scripts/task_progress_reporter.py" \
  --task "CAMS ADS 温室气体更新" \
  --default-stage "清理异常残留" \
  --watch-root "$PRODUCER_ROOT/ads_staging" \
  --log-file "$LOG_DIR/openmeteo_cams_ads_schedule.log"
