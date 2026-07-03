#!/usr/bin/env bash
set -euo pipefail

RUN="${1:-${WEATHER_GFS_RUN:-}}"
if [[ -z "$RUN" ]]; then
  printf '%s\n' "Usage: run_gfs_production_cycle.sh YYYYMMDDHH" >&2
  exit 2
fi

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_GFS_LOCK_FILE:-/tmp/weather_openmeteo_gfs_cycle.lock}"

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
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS] previous job still running, skip."
    exit 0
  }

  cd "$APP_DIR"
  if [[ "${WEATHER_GIT_PULL:-false}" == "true" ]]; then
    git pull --ff-only
  fi

  export WEATHER_GFS_RUN="$RUN"
  layer_start_hour="$(run_to_utc_layer_start "$RUN")"
  export WEATHER_OPENMETEO_GFS_RUN="$layer_start_hour"
  export WEATHER_OPENMETEO_LAYER_START_HOUR="$layer_start_hour"
  export WEATHER_OPENMETEO_LAYER_FRAME_COUNT="121"
  unset WEATHER_OPENMETEO_LAYER_END_HOUR
  DATA_DIR="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}"
  GFS_LATEST_BACKUP_DIR="$DATA_DIR/gfs_latest_backup_$$"
  gfs_publish_ok=false
  export WEATHER_OPENMETEO_PUBLIC_DATA_DIR="${WEATHER_OPENMETEO_PUBLIC_DATA_DIR:-/opt/1panel/apps/weather/data}"
  export WEATHER_OPENMETEO_LAYER_ROOT_DIR="${WEATHER_OPENMETEO_LAYER_ROOT_DIR:-$APP_DIR/data/openmeteo_layers}"

  cleanup_gfs_generated_products() {
    rm -rf \
      "$DATA_DIR/ncep_gfs013" \
      "$DATA_DIR/ncep_gfs025" \
      "$DATA_DIR/data_run/ncep_gfs013" \
      "$DATA_DIR/data_run/ncep_gfs025"
  }

  backup_gfs_latest_refs() {
    local domain
    local src
    mkdir -p "$GFS_LATEST_BACKUP_DIR"
    for domain in ncep_gfs013 ncep_gfs025; do
      src="$DATA_DIR/data_run/$domain/latest.json"
      if [[ -f "$src" ]]; then
        cp -f "$src" "$GFS_LATEST_BACKUP_DIR/$domain.latest.json"
      else
        touch "$GFS_LATEST_BACKUP_DIR/$domain.missing"
      fi
    done
  }

  restore_gfs_latest_refs() {
    local domain
    local dst
    if [[ ! -d "$GFS_LATEST_BACKUP_DIR" ]]; then
      return
    fi
    for domain in ncep_gfs013 ncep_gfs025; do
      dst="$DATA_DIR/data_run/$domain/latest.json"
      if [[ -f "$GFS_LATEST_BACKUP_DIR/$domain.latest.json" ]]; then
        mkdir -p "$(dirname "$dst")"
        cp -f "$GFS_LATEST_BACKUP_DIR/$domain.latest.json" "$dst"
      elif [[ -f "$GFS_LATEST_BACKUP_DIR/$domain.missing" ]]; then
        rm -f "$dst"
      fi
    done
  }

  cleanup_gfs_latest_backup() {
    rm -rf "$GFS_LATEST_BACKUP_DIR"
  }

  on_gfs_production_exit() {
    local status="$?"
    if [[ "$gfs_publish_ok" != "true" ]]; then
      restore_gfs_latest_refs
    fi
    cleanup_gfs_latest_backup
    exit "$status"
  }
  trap on_gfs_production_exit EXIT

  backup_gfs_latest_refs
  cleanup_gfs_generated_products

  download_start="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$download_start [OPENMETEO_GFS] download runtime data run=$RUN start=$download_start"
  bash scripts/download_openmeteo_gfs_data.sh
  download_end="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$download_end [OPENMETEO_GFS] download runtime data run=$RUN end=$download_end"

  python3 scripts/validate_openmeteo_latest_run.py \
    --data-dir "$DATA_DIR" \
    --run "$RUN" \
    --domains ncep_gfs013,ncep_gfs025 \
    --min-frames "$(( ${WEATHER_GFS_MAX_FORECAST_HOUR:-120} + 1 ))"

  layer_start="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$layer_start [OPENMETEO_GFS] build GFS layer products start=$layer_start"
  bash scripts/build_openmeteo_gfs_layers.sh
  layer_end="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$layer_end [OPENMETEO_GFS] build GFS layer products end=$layer_end"

  gfs_publish_ok=true
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS] completed run=$RUN"
} 9>"$LOCK_FILE" >> "$LOG_DIR/openmeteo_gfs_cycle.log" 2>&1
