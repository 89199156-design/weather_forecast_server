#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env
RUN="${1:-${WEATHER_GFS_RUN:-}}"
if [[ -z "$RUN" ]]; then
  printf '%s\n' "Usage: run_gfs_om_production_cycle.sh YYYYMMDDHH" >&2
  exit 2
fi
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_GFS_LOCK_FILE:-/tmp/weather_openmeteo_gfs_cycle.lock}"
GLOBAL_LOCK_FILE="${WEATHER_OPENMETEO_GLOBAL_LOCK_FILE:-/tmp/weather_openmeteo_production.lock}"
PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"
RESUME_STAGING="${WEATHER_OM_GFS_RESUME_STAGING:-}"
FORCE_REUSED_DOWNLOAD="${WEATHER_OM_GFS_FORCE_REUSED_DOWNLOAD:-false}"
REPAIR_SURFACE_ONLY="${WEATHER_OM_GFS_REPAIR_SURFACE_ONLY:-false}"
REPAIR_PRESSURE_ONLY="${WEATHER_OM_GFS_REPAIR_PRESSURE_ONLY:-false}"
COVERAGE_REVISION="${WEATHER_OM_GFS_COVERAGE_REVISION:-}"
SAME_RUN_COVERAGE_REVISION="${WEATHER_OM_GFS_SAME_RUN_COVERAGE_REVISION:-three-short-two-full-v1}"
LATEST_MAX_FORECAST_HOUR="${WEATHER_GFS_REQUIRED_MAX_FORECAST_HOUR:-384}"
SOURCE_RUN_COUNT="${WEATHER_GFS_REQUIRED_SOURCE_RUN_COUNT:-5}"
FULL_RUN_COUNT="${WEATHER_GFS_REQUIRED_FULL_RUN_COUNT:-2}"
HISTORY_MAX_FORECAST_HOUR="${WEATHER_GFS_REQUIRED_HISTORY_FORECAST_HOUR:-5}"
LOCAL_UTC_OFFSET_HOURS="${WEATHER_GFS_LOCAL_UTC_OFFSET_HOURS:-8}"
MIN_PUBLIC_HOURS="${WEATHER_GFS_MIN_PUBLIC_HOURS:-300}"
KEEP_COVERAGES="${WEATHER_OM_GFS_KEEP_COVERAGES:-1}"
GFS_UPPER_LEVELS="${WEATHER_GFS_REQUIRED_UPPER_LEVELS:-1000,975,950,925,900,850,800,750,700,650,600,550,500,450,400,350,300,250,200,150,100,50}"
GFS_UPPER_LEVEL_VARIABLES="${WEATHER_GFS_UPPER_LEVEL_VARIABLES:-temperature,wind_u_component,wind_v_component,geopotential_height,cloud_cover,relative_humidity,vertical_velocity}"
GFS_STORAGE_LEFT_LON="${WEATHER_GFS_STORAGE_LEFT_LON:-69}"
GFS_STORAGE_RIGHT_LON="${WEATHER_GFS_STORAGE_RIGHT_LON:-141}"
GFS_STORAGE_BOTTOM_LAT="${WEATHER_GFS_STORAGE_BOTTOM_LAT:--1}"
GFS_STORAGE_TOP_LAT="${WEATHER_GFS_STORAGE_TOP_LAT:-59}"

# The producer keeps a one-degree storage halo around the public 70-140E,
# 0-58N service area. Do not inherit older generic region/pressure settings:
# the downloader and validator must always use the same production contract.
export WEATHER_GFS_DOWNLOAD_MODE="${WEATHER_GFS_DOWNLOAD_MODE:-nomads-region}"
if [[ "$WEATHER_GFS_DOWNLOAD_MODE" != "nomads-region" ]]; then
  printf '%s\n' \
    "Production GFS requires NOMADS server-side regional cropping; refusing WEATHER_GFS_DOWNLOAD_MODE=$WEATHER_GFS_DOWNLOAD_MODE" >&2
  exit 2
fi
export WEATHER_REGION_LEFT_LON="$GFS_STORAGE_LEFT_LON"
export WEATHER_REGION_RIGHT_LON="$GFS_STORAGE_RIGHT_LON"
export WEATHER_REGION_BOTTOM_LAT="$GFS_STORAGE_BOTTOM_LAT"
export WEATHER_REGION_TOP_LAT="$GFS_STORAGE_TOP_LAT"
export WEATHER_GFS_UPPER_LEVELS="$GFS_UPPER_LEVELS"
if is_truthy "$REPAIR_SURFACE_ONLY" && is_truthy "$REPAIR_PRESSURE_ONLY"; then
  printf '%s\n' "GFS surface-only and pressure-only repair modes are mutually exclusive" >&2
  exit 2
fi
if is_truthy "$REPAIR_PRESSURE_ONLY"; then
  export WEATHER_GFS_SKIP_GFS013=true
  export WEATHER_GFS_SKIP_GFS025=false
  export WEATHER_GFS_SKIP_GFS025_SURFACE=true
  export WEATHER_GFS_SKIP_GFS025_UPPER_LEVELS=false
fi
PARTIAL_REPAIR=false
if is_truthy "$REPAIR_SURFACE_ONLY" || is_truthy "$REPAIR_PRESSURE_ONLY"; then
  PARTIAL_REPAIR=true
fi

mkdir -p "$LOG_DIR" "$PRODUCER_ROOT/staging"

{
  flock -n 9 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_OM] previous job still running, skip."
    exit 0
  }

  exec 8>"$GLOBAL_LOCK_FILE"
  flock -n 8 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_OM] another Open-Meteo production cycle is running, skip."
    exit 0
  }

  cd "$APP_DIR"
  read -r SOURCE_RUNS PUBLIC_START_UTC PUBLIC_END_UTC PUBLIC_HOURS LOCAL_DAY_START_UTC < <(
    python3 scripts/model_source_run_plan.py \
      --run "$RUN" \
      --cadence-hours 6 \
      --source-run-count "$SOURCE_RUN_COUNT" \
      --historical-max-forecast-hour "$HISTORY_MAX_FORECAST_HOUR" \
      --local-utc-offset-hours "$LOCAL_UTC_OFFSET_HOURS" \
      --latest-max-forecast-hour "$LATEST_MAX_FORECAST_HOUR" \
      --full-run-count "$FULL_RUN_COUNT" \
      --format fields
  )

  STAGING_DIR="$PRODUCER_ROOT/staging/gfs_${RUN}_$$"
  cleanup_staging() {
    if [[ -n "${STAGING_DIR:-}" && "$STAGING_DIR" == "$PRODUCER_ROOT/staging/"* ]]; then
      rm -rf -- "$STAGING_DIR"
    fi
  }
  trap cleanup_staging EXIT
  RESUME_SOURCE=""
  if [[ -n "$RESUME_STAGING" ]]; then
    mkdir -p "$PRODUCER_ROOT/resume"
    RESUME_ROOT="$(readlink -f -- "$PRODUCER_ROOT/resume")"
    RESUME_SOURCE="$(readlink -f -- "$RESUME_STAGING")"
    if [[ ! -d "$RESUME_SOURCE" \
      || "$(dirname -- "$RESUME_SOURCE")" != "$RESUME_ROOT" \
      || "$(basename -- "$RESUME_SOURCE")" != gfs_"$RUN"_* ]]; then
      echo "unsafe or mismatched GFS resume staging: $RESUME_STAGING" >&2
      exit 1
    fi
    cp -al -- "$RESUME_SOURCE" "$STAGING_DIR"
    REUSED_SOURCE_RUNS="$(python3 -c 'import sys; print(",".join(sys.argv[1].split(",")[:-1]))' "$SOURCE_RUNS")"
    SEEDED_LATEST_RUN=""
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_OM] resumed staging=$RESUME_SOURCE"
  else
    SEED_JSON="$(python3 scripts/seed_native_om_staging.py \
      --output-root "$PRODUCER_ROOT" \
      --staging-dir "$STAGING_DIR" \
      --group gfs \
      --source-runs "$SOURCE_RUNS")"
    REUSED_SOURCE_RUNS="$(python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)["reused_source_runs"]))' <<<"$SEED_JSON")"
    SEEDED_LATEST_RUN="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("seeded_latest_complete_run") or "")' <<<"$SEED_JSON")"
  fi
  if [[ -z "$COVERAGE_REVISION" && "$SEEDED_LATEST_RUN" == "$RUN" ]]; then
    COVERAGE_REVISION="$SAME_RUN_COVERAGE_REVISION"
  fi

  ACTIVE_DATA_DIR="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/point}"
  if [[ ! -d "$STAGING_DIR/copernicus_dem90" && -d "$ACTIVE_DATA_DIR/copernicus_dem90" ]]; then
    cp -al "$ACTIVE_DATA_DIR/copernicus_dem90" "$STAGING_DIR/"
  fi
  prepare_openmeteo_staging_permissions "$STAGING_DIR"

  export WEATHER_OPENMETEO_DATA_DIR="$STAGING_DIR"
  validate_staged_gfs_run() {
    local source_run="$1"
    local max_forecast_hour="$2"
    local required_gfs013="${WEATHER_GFS013_REQUIRED_DATA_RUN_VARIABLES:-}"
    local required_gfs025="${WEATHER_GFS025_REQUIRED_DATA_RUN_VARIABLES:-}"
    PYTHONPATH="$APP_DIR/scripts${PYTHONPATH:+:$PYTHONPATH}" python3 - \
      "$STAGING_DIR" \
      "$source_run" \
      "$max_forecast_hour" \
      "$required_gfs013" \
      "$required_gfs025" \
      "$GFS_UPPER_LEVELS" \
      "$GFS_UPPER_LEVEL_VARIABLES" <<'PY'
from argparse import Namespace
import json
from pathlib import Path
import sys

from publish_native_om_coverage import (
    DEFAULT_GFS013_REQUIRED,
    DEFAULT_GFS025_REQUIRED,
    required_variables_by_domain,
    validate_gfs_retained_run,
)

staging = Path(sys.argv[1])
run = sys.argv[2]
max_forecast_hour = int(sys.argv[3])
requirements = required_variables_by_domain(
    Namespace(
        required_gfs013_variables=sys.argv[4] or DEFAULT_GFS013_REQUIRED,
        required_gfs025_variables=sys.argv[5] or DEFAULT_GFS025_REQUIRED,
        required_pressure_levels=sys.argv[6],
        required_pressure_variables=sys.argv[7],
    )
)
try:
    validate_gfs_retained_run(staging, run, max_forecast_hour, requirements)
except (OSError, ValueError, json.JSONDecodeError) as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(1)
PY
  }
  restore_latest_metadata() {
    local source_run="$1"
    local relative="${source_run:0:4}/${source_run:4:2}/${source_run:6:2}/${source_run:8:2}00Z"
    local domain
    local source
    local latest
    for domain in ncep_gfs013 ncep_gfs025; do
      source="$STAGING_DIR/data_run/$domain/$relative/meta.json"
      latest="$STAGING_DIR/data_run/$domain/latest.json"
      if [[ ! -s "$source" ]]; then
        echo "missing latest GFS run metadata: $source" >&2
        return 1
      fi
      cp -- "$source" "$latest.tmp.$$"
      mv -f -- "$latest.tmp.$$" "$latest"
    done
  }
  preserve_run_metadata() {
    local source_run="$1"
    local relative="${source_run:0:4}/${source_run:4:2}/${source_run:6:2}/${source_run:8:2}00Z"
    local domain
    local current
    local original
    for domain in ncep_gfs013 ncep_gfs025; do
      current="$STAGING_DIR/data_run/$domain/$relative/meta.json"
      original="$STAGING_DIR/.repair_metadata/$domain/${source_run}.json"
      mkdir -p "$(dirname "$original")"
      cp -- "$current" "$original"
    done
  }
  merge_run_metadata() {
    local source_run="$1"
    local relative="${source_run:0:4}/${source_run:4:2}/${source_run:6:2}/${source_run:8:2}00Z"
    local domain
    for domain in ncep_gfs013 ncep_gfs025; do
      python3 scripts/merge_native_run_metadata.py \
        --original "$STAGING_DIR/.repair_metadata/$domain/${source_run}.json" \
        --current "$STAGING_DIR/data_run/$domain/$relative/meta.json" \
        --latest "$STAGING_DIR/data_run/$domain/latest.json"
    done
  }
  IFS=',' read -ra PLANNED_SOURCE_RUNS <<< "$SOURCE_RUNS"
  SHORT_RUN_COUNT=$((${#PLANNED_SOURCE_RUNS[@]} - FULL_RUN_COUNT))
  if (( FULL_RUN_COUNT != 2 || SHORT_RUN_COUNT != 3 )); then
    echo "GFS production requires exactly three short runs and two complete runs" >&2
    exit 1
  fi
  for ((SOURCE_INDEX = 0; SOURCE_INDEX < ${#PLANNED_SOURCE_RUNS[@]} - 1; SOURCE_INDEX++)); do
    SOURCE_RUN="${PLANNED_SOURCE_RUNS[$SOURCE_INDEX]}"
    SOURCE_MAX_FORECAST_HOUR="$HISTORY_MAX_FORECAST_HOUR"
    SOURCE_ROLE="short-history"
    if (( SOURCE_INDEX >= SHORT_RUN_COUNT )); then
      SOURCE_MAX_FORECAST_HOUR="$LATEST_MAX_FORECAST_HOUR"
      SOURCE_ROLE="previous-complete"
    fi
    if ! is_truthy "$FORCE_REUSED_DOWNLOAD" \
      && [[ ",$REUSED_SOURCE_RUNS," == *",$SOURCE_RUN,"* ]]; then
      if validate_staged_gfs_run "$SOURCE_RUN" "$SOURCE_MAX_FORECAST_HOUR"; then
        echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_OM] reuse validated role=$SOURCE_ROLE run=$SOURCE_RUN horizon=$SOURCE_MAX_FORECAST_HOUR"
        continue
      fi
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_OM] repair invalid role=$SOURCE_ROLE run=$SOURCE_RUN horizon=$SOURCE_MAX_FORECAST_HOUR"
    fi
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_OM] download role=$SOURCE_ROLE run=$SOURCE_RUN horizon=$SOURCE_MAX_FORECAST_HOUR"
    if is_truthy "$PARTIAL_REPAIR"; then
      preserve_run_metadata "$SOURCE_RUN"
    fi
    WEATHER_GFS_RUN="$SOURCE_RUN" \
    WEATHER_GFS_MAX_FORECAST_HOUR="$SOURCE_MAX_FORECAST_HOUR" \
      bash scripts/download_openmeteo_gfs_data.sh

    if is_truthy "$PARTIAL_REPAIR"; then
      merge_run_metadata "$SOURCE_RUN"
    fi

    validate_staged_gfs_run "$SOURCE_RUN" "$SOURCE_MAX_FORECAST_HOUR"
  done

  REUSE_LATEST=false
  if ! is_truthy "$FORCE_REUSED_DOWNLOAD" \
    && [[ "$SEEDED_LATEST_RUN" == "$RUN" && ",$REUSED_SOURCE_RUNS," == *",$RUN,"* ]] \
    && validate_staged_gfs_run "$RUN" "$LATEST_MAX_FORECAST_HOUR"; then
    REUSE_LATEST=true
  fi
  if is_truthy "$REUSE_LATEST"; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_OM] reuse validated latest run=$RUN horizon=$LATEST_MAX_FORECAST_HOUR"
  else
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_OM] latest run=$RUN horizon=$LATEST_MAX_FORECAST_HOUR"
    if is_truthy "$PARTIAL_REPAIR"; then
      preserve_run_metadata "$RUN"
    fi
    WEATHER_GFS_RUN="$RUN" \
    WEATHER_GFS_MAX_FORECAST_HOUR="$LATEST_MAX_FORECAST_HOUR" \
      bash scripts/download_openmeteo_gfs_data.sh
    if is_truthy "$PARTIAL_REPAIR"; then
      merge_run_metadata "$RUN"
    fi
  fi

  restore_latest_metadata "$RUN"
  python3 scripts/validate_openmeteo_latest_run.py \
    --data-dir "$STAGING_DIR" \
    --run "$RUN" \
    --domains ncep_gfs013,ncep_gfs025 \
    --min-frames 0 \
    --gfs-max-forecast-hour "$LATEST_MAX_FORECAST_HOUR" \
    --required-gfs-pressure-domain ncep_gfs025 \
    --required-gfs-pressure-levels "$GFS_UPPER_LEVELS" \
    --required-gfs-pressure-variables "$GFS_UPPER_LEVEL_VARIABLES"

  for ((SOURCE_INDEX = 0; SOURCE_INDEX < ${#PLANNED_SOURCE_RUNS[@]}; SOURCE_INDEX++)); do
    SOURCE_RUN="${PLANNED_SOURCE_RUNS[$SOURCE_INDEX]}"
    SOURCE_MAX_FORECAST_HOUR="$HISTORY_MAX_FORECAST_HOUR"
    if (( SOURCE_INDEX >= SHORT_RUN_COUNT )); then
      SOURCE_MAX_FORECAST_HOUR="$LATEST_MAX_FORECAST_HOUR"
    fi
    validate_staged_gfs_run "$SOURCE_RUN" "$SOURCE_MAX_FORECAST_HOUR"
  done

  if is_truthy "$PARTIAL_REPAIR"; then
    rm -rf -- "$STAGING_DIR/.repair_metadata"
  fi

  python3 scripts/prune_native_om_runs.py \
    --data-dir "$STAGING_DIR" \
    --domains ncep_gfs013,ncep_gfs025 \
    --retained-runs "$SOURCE_RUNS"

  COVERAGE_REVISION_ARGS=()
  if [[ -n "$COVERAGE_REVISION" ]]; then
    COVERAGE_REVISION_ARGS=(--coverage-revision "$COVERAGE_REVISION")
  fi

  python3 scripts/publish_native_om_coverage.py \
    --group gfs \
    --staging-dir "$STAGING_DIR" \
    --output-root "$PRODUCER_ROOT" \
    --latest-run "$RUN" \
    --source-runs "$SOURCE_RUNS" \
    --full-run-count "$FULL_RUN_COUNT" \
    --historical-max-forecast-hour "$HISTORY_MAX_FORECAST_HOUR" \
    --latest-max-forecast-hour "$LATEST_MAX_FORECAST_HOUR" \
    --public-start-utc "$PUBLIC_START_UTC" \
    --public-end-utc "$PUBLIC_END_UTC" \
    --public-hours "$PUBLIC_HOURS" \
    --local-day-start-utc "$LOCAL_DAY_START_UTC" \
    --min-public-hours "$MIN_PUBLIC_HOURS" \
    "${COVERAGE_REVISION_ARGS[@]}" \
    --keep-coverages "$KEEP_COVERAGES"

  if [[ -n "$RESUME_SOURCE" ]]; then
    rm -rf -- "$RESUME_SOURCE"
  fi

  trap - EXIT
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS_OM] completed run=$RUN sources=$SOURCE_RUNS"
} 9>"$LOCK_FILE" >> "$LOG_DIR/openmeteo_gfs_om_cycle.log" 2>&1
