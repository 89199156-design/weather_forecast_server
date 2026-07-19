#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env
RUN="${1:-${WEATHER_CAMS_RUN:-}}"
if [[ -z "$RUN" ]]; then
  printf '%s\n' "Usage: run_cams_om_production_cycle.sh YYYYMMDDHH" >&2
  exit 2
fi
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_CAMS_FTP_LOCK_FILE:-/tmp/weather_openmeteo_cams_ftp_cycle.lock}"
GLOBAL_LOCK_FILE="${WEATHER_OPENMETEO_GLOBAL_LOCK_FILE:-/tmp/weather_openmeteo_production.lock}"
PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"
KEEP_COVERAGES="${WEATHER_OM_CAMS_KEEP_COVERAGES:-1}"
SOURCE_RUN_COUNT="${WEATHER_CAMS_REQUIRED_SOURCE_RUN_COUNT:-3}"
MAX_FORECAST_HOUR="${WEATHER_CAMS_REQUIRED_MAX_FORECAST_HOUR:-120}"
GREENHOUSE_SOURCE_RUN_COUNT="${WEATHER_CAMS_GREENHOUSE_SOURCE_RUN_COUNT:-3}"
GREENHOUSE_MAX_FORECAST_HOUR="${WEATHER_CAMS_GREENHOUSE_MAX_FORECAST_HOUR:-120}"
FORCE_GREENHOUSE_DOWNLOAD="${WEATHER_CAMS_FORCE_GREENHOUSE_DOWNLOAD:-false}"
COVERAGE_REVISION="${WEATHER_CAMS_COVERAGE_REVISION:-greenhouse-region-v3}"
LOCAL_UTC_OFFSET_HOURS="${WEATHER_CAMS_LOCAL_UTC_OFFSET_HOURS:-8}"
CAMS_STORAGE_LEFT_LON="${WEATHER_CAMS_STORAGE_LEFT_LON:-69}"
CAMS_STORAGE_RIGHT_LON="${WEATHER_CAMS_STORAGE_RIGHT_LON:-141}"
CAMS_STORAGE_BOTTOM_LAT="${WEATHER_CAMS_STORAGE_BOTTOM_LAT:--1}"
CAMS_STORAGE_TOP_LAT="${WEATHER_CAMS_STORAGE_TOP_LAT:-59}"

export WEATHER_REGION_LEFT_LON="$CAMS_STORAGE_LEFT_LON"
export WEATHER_REGION_RIGHT_LON="$CAMS_STORAGE_RIGHT_LON"
export WEATHER_REGION_BOTTOM_LAT="$CAMS_STORAGE_BOTTOM_LAT"
export WEATHER_REGION_TOP_LAT="$CAMS_STORAGE_TOP_LAT"

mkdir -p "$LOG_DIR" "$PRODUCER_ROOT/staging"

{
  flock -n 9 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_OM] previous job still running, skip."
    exit 0
  }

  exec 8>"$GLOBAL_LOCK_FILE"
  flock -n 8 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_OM] another Open-Meteo production cycle is running, skip."
    exit 0
  }

  cd "$APP_DIR"
  read -r SOURCE_RUNS PUBLIC_START_UTC PUBLIC_END_UTC PUBLIC_HOURS LOCAL_DAY_START_UTC < <(
    python3 scripts/model_source_run_plan.py \
      --run "$RUN" \
      --cadence-hours 12 \
      --source-run-count "$SOURCE_RUN_COUNT" \
      --historical-max-forecast-hour "$MAX_FORECAST_HOUR" \
      --latest-max-forecast-hour "$MAX_FORECAST_HOUR" \
      --local-utc-offset-hours "$LOCAL_UTC_OFFSET_HOURS" \
      --format fields
  )
  GREENHOUSE_SOURCE_RUNS="$(python3 - "$RUN" "$GREENHOUSE_SOURCE_RUN_COUNT" <<'PY'
from datetime import datetime, timedelta, timezone
import sys

run = datetime.strptime(sys.argv[1], "%Y%m%d%H").replace(tzinfo=timezone.utc)
count = int(sys.argv[2])
if count != 3:
    raise SystemExit("CAMS greenhouse must retain exactly three daily runs")
# The Open-Meteo CAMS release pairs the current CAMS cycle with the latest
# greenhouse cycle that is publishable in the same release. The greenhouse
# product trails the CAMS cycle by two UTC days. Retain that current run plus
# its two preceding daily runs so null fallback never consumes future data.
latest = run.replace(hour=0) - timedelta(days=2)
print(",".join(
    (latest - timedelta(days=offset)).strftime("%Y%m%d00")
    for offset in range(count - 1, -1, -1)
))
PY
)"
  STAGING_DIR="$PRODUCER_ROOT/staging/cams_${RUN}_$$"
  cleanup_staging() {
    if [[ -n "${STAGING_DIR:-}" && "$STAGING_DIR" == "$PRODUCER_ROOT/staging/"* ]]; then
      rm -rf -- "$STAGING_DIR"
    fi
  }
  trap cleanup_staging EXIT
  SEED_JSON="$(python3 scripts/seed_native_om_staging.py \
    --output-root "$PRODUCER_ROOT" \
    --staging-dir "$STAGING_DIR" \
    --group cams \
    --source-runs "$SOURCE_RUNS")"
  REUSED_SOURCE_RUNS="$(python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)["reused_source_runs"]))' <<<"$SEED_JSON")"
  prepare_openmeteo_staging_permissions "$STAGING_DIR"
  export WEATHER_OPENMETEO_DATA_DIR="$STAGING_DIR"

  validate_staged_cams_run() {
    local source_run="$1"
    local required_variables="${WEATHER_CAMS_VARIABLES:-}"
    PYTHONPATH="$APP_DIR/scripts${PYTHONPATH:+:$PYTHONPATH}" python3 - \
      "$STAGING_DIR" "$source_run" "$required_variables" <<'PY'
from pathlib import Path
import sys

from publish_native_cams_coverage import DEFAULT_CAMS_REQUIRED_VARIABLES
from publish_native_om_coverage import validate_run_metadata

staging = Path(sys.argv[1])
source_run = sys.argv[2]
required = {
    item.strip()
    for item in (sys.argv[3] or DEFAULT_CAMS_REQUIRED_VARIABLES).split(",")
    if item.strip()
}
meta = validate_run_metadata(staging, "cams_global", source_run, list(range(121)))
available = set(meta["variables"])
missing = sorted(required - available)
if missing:
    raise SystemExit(
        f"cams_global run {source_run} is missing required variables: {','.join(missing)}"
    )
validate_run_metadata(
    staging,
    "cams_global",
    source_run,
    list(range(121)),
    {variable: 121 for variable in meta["variables"]},
)
PY
  }

  validate_staged_greenhouse_run() {
    local source_run="$1"
    local required_variables="${WEATHER_CAMS_GREENHOUSE_VARIABLES:-carbon_monoxide}"
    PYTHONPATH="$APP_DIR/scripts${PYTHONPATH:+:$PYTHONPATH}" python3 - \
      "$STAGING_DIR" "$source_run" "$required_variables" <<'PY'
from pathlib import Path
import sys

from publish_native_om_coverage import validate_run_metadata

staging = Path(sys.argv[1])
source_run = sys.argv[2]
required = {item.strip() for item in sys.argv[3].split(",") if item.strip()}
hours = list(range(0, 121, 3))
meta = validate_run_metadata(
    staging,
    "cams_global_greenhouse_gases",
    source_run,
    hours,
)
available = set(meta["variables"])
missing = sorted(required - available)
if missing:
    raise SystemExit(
        "cams_global_greenhouse_gases run "
        f"{source_run} is missing required variables: {','.join(missing)}"
    )
validate_run_metadata(
    staging,
    "cams_global_greenhouse_gases",
    source_run,
    hours,
    {variable: 41 for variable in meta["variables"]},
)
PY
  }

  restore_cams_latest_metadata() {
    local source_run="$1"
    local relative="${source_run:0:4}/${source_run:4:2}/${source_run:6:2}/${source_run:8:2}00Z"
    local source="$STAGING_DIR/data_run/cams_global/$relative/meta.json"
    local latest="$STAGING_DIR/data_run/cams_global/latest.json"
    if [[ ! -s "$source" ]]; then
      echo "missing latest CAMS run metadata: $source" >&2
      return 1
    fi
    cp -- "$source" "$latest.tmp.$$"
    mv -f -- "$latest.tmp.$$" "$latest"
  }

  IFS=',' read -ra PLANNED_SOURCE_RUNS <<< "$SOURCE_RUNS"
  for SOURCE_RUN in "${PLANNED_SOURCE_RUNS[@]:0:${#PLANNED_SOURCE_RUNS[@]}-1}"; do
    if [[ ",$REUSED_SOURCE_RUNS," == *",$SOURCE_RUN,"* ]]; then
      if validate_staged_cams_run "$SOURCE_RUN"; then
        echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_OM] reuse validated history run=$SOURCE_RUN"
        continue
      fi
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_OM] repair invalid history run=$SOURCE_RUN"
    fi
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_OM] history run=$SOURCE_RUN horizon=$MAX_FORECAST_HOUR"
    WEATHER_CAMS_RUN="$SOURCE_RUN" \
    WEATHER_CAMS_MAX_FORECAST_HOUR="$MAX_FORECAST_HOUR" \
      bash scripts/download_openmeteo_cams_data.sh
    validate_staged_cams_run "$SOURCE_RUN"
  done

  if [[ ",$REUSED_SOURCE_RUNS," == *",$RUN,"* ]] \
    && validate_staged_cams_run "$RUN"; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_OM] reuse validated latest run=$RUN"
  else
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_OM] latest run=$RUN horizon=$MAX_FORECAST_HOUR"
    WEATHER_CAMS_RUN="$RUN" \
    WEATHER_CAMS_MAX_FORECAST_HOUR="$MAX_FORECAST_HOUR" \
      bash scripts/download_openmeteo_cams_data.sh
    validate_staged_cams_run "$RUN"
  fi

  restore_cams_latest_metadata "$RUN"

  python3 scripts/validate_openmeteo_latest_run.py \
    --data-dir "$STAGING_DIR" \
    --run "$RUN" \
    --domains cams_global \
    --min-frames "$((MAX_FORECAST_HOUR + 1))"

  IFS=',' read -ra PLANNED_GREENHOUSE_RUNS <<< "$GREENHOUSE_SOURCE_RUNS"
  if is_truthy "$FORCE_GREENHOUSE_DOWNLOAD"; then
    # This is an explicit repair-only path. Unlink greenhouse files from the
    # private staging tree before rebuilding all three retained runs; the
    # immutable current coverage remains untouched until atomic publication.
    rm -rf -- "$STAGING_DIR/cams_global_greenhouse_gases"
    for GREENHOUSE_RUN in "${PLANNED_GREENHOUSE_RUNS[@]}"; do
      GREENHOUSE_RUN_DIR="$STAGING_DIR/data_run/cams_global_greenhouse_gases/${GREENHOUSE_RUN:0:4}/${GREENHOUSE_RUN:4:2}/${GREENHOUSE_RUN:6:2}/${GREENHOUSE_RUN:8:2}00Z"
      rm -rf -- "$GREENHOUSE_RUN_DIR"
    done
    rm -f -- "$STAGING_DIR/data_run/cams_global_greenhouse_gases/latest.json"
  fi
  for GREENHOUSE_RUN in "${PLANNED_GREENHOUSE_RUNS[@]}"; do
    if validate_staged_greenhouse_run "$GREENHOUSE_RUN"; then
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_OM] reuse validated greenhouse run=$GREENHOUSE_RUN"
      continue
    fi
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_OM] greenhouse run=$GREENHOUSE_RUN horizon=$GREENHOUSE_MAX_FORECAST_HOUR"
    WEATHER_CAMS_GREENHOUSE_RUN="$GREENHOUSE_RUN" \
      bash scripts/download_openmeteo_cams_greenhouse_data.sh
    validate_staged_greenhouse_run "$GREENHOUSE_RUN"
  done

  # A repair may download an older missing run while reusing the newest run.
  # Restore latest.json from the newest retained run with an atomic replacement;
  # never truncate the hard-linked file inherited from the current coverage.
  GREENHOUSE_LATEST_RUN="${PLANNED_GREENHOUSE_RUNS[-1]}"
  GREENHOUSE_LATEST_DIR="$STAGING_DIR/data_run/cams_global_greenhouse_gases/${GREENHOUSE_LATEST_RUN:0:4}/${GREENHOUSE_LATEST_RUN:4:2}/${GREENHOUSE_LATEST_RUN:6:2}/${GREENHOUSE_LATEST_RUN:8:2}00Z"
  GREENHOUSE_LATEST_JSON="$STAGING_DIR/data_run/cams_global_greenhouse_gases/latest.json"
  cp "$GREENHOUSE_LATEST_DIR/meta.json" "$GREENHOUSE_LATEST_JSON.tmp.$$"
  mv -f "$GREENHOUSE_LATEST_JSON.tmp.$$" "$GREENHOUSE_LATEST_JSON"

  python3 scripts/validate_openmeteo_latest_run.py \
    --data-dir "$STAGING_DIR" \
    --run "$GREENHOUSE_LATEST_RUN" \
    --domains cams_global_greenhouse_gases \
    --min-frames "$((GREENHOUSE_MAX_FORECAST_HOUR / 3 + 1))"

  python3 scripts/prune_native_om_runs.py \
    --data-dir "$STAGING_DIR" \
    --domains cams_global \
    --retained-runs "$SOURCE_RUNS"

  python3 scripts/prune_native_om_runs.py \
    --data-dir "$STAGING_DIR" \
    --domains cams_global_greenhouse_gases \
    --retained-runs "$GREENHOUSE_SOURCE_RUNS"

  python3 scripts/publish_native_cams_coverage.py \
    --staging-dir "$STAGING_DIR" \
    --output-root "$PRODUCER_ROOT" \
    --run "$RUN" \
    --source-runs "$SOURCE_RUNS" \
    --greenhouse-source-runs "$GREENHOUSE_SOURCE_RUNS" \
    --latest-max-forecast-hour "$MAX_FORECAST_HOUR" \
    --public-start-utc "$PUBLIC_START_UTC" \
    --public-end-utc "$PUBLIC_END_UTC" \
    --public-hours "$PUBLIC_HOURS" \
    --local-day-start-utc "$LOCAL_DAY_START_UTC" \
    --keep-coverages "$KEEP_COVERAGES" \
    --coverage-revision "$COVERAGE_REVISION"

  trap - EXIT
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_OM] completed run=$RUN sources=$SOURCE_RUNS greenhouse_sources=$GREENHOUSE_SOURCE_RUNS"
} 9>"$LOCK_FILE" >> "$LOG_DIR/openmeteo_cams_om_cycle.log" 2>&1
