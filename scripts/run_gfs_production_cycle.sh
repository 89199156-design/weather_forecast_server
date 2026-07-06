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
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS] previous job still running, skip."
    exit 0
  }

  exec 8>"$GLOBAL_LOCK_FILE"
  flock -n 8 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS] another Open-Meteo production cycle is running, skip."
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
  GFS_UPPER_LEVELS="${WEATHER_GFS_UPPER_LEVELS:-1000,975,950,925,900,850,800,750,700,650,600,550,500,400,300,200}"
  GFS_UPPER_LEVEL_VARIABLES="${WEATHER_GFS_UPPER_LEVEL_VARIABLES:-temperature,wind_u_component,wind_v_component,geopotential_height,cloud_cover,relative_humidity,vertical_velocity}"
  ACTIVE_DATA_DIR="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}"
  ACTIVE_PUBLIC_DATA_DIR="${WEATHER_OPENMETEO_PUBLIC_DATA_DIR:-/opt/1panel/apps/weather/data}"
  ACTIVE_LAYER_ROOT_DIR="${WEATHER_OPENMETEO_LAYER_ROOT_DIR:-$APP_DIR/data/openmeteo_layers}"
  GFS_OUTPUT_DIR="${WEATHER_OPENMETEO_LAYER_DIR:-$ACTIVE_LAYER_ROOT_DIR/gfs013_surface}"
  GFS_STAGING_DATA_DIR="$ACTIVE_DATA_DIR/gfs_staging_${RUN}_$$"
  GFS_STAGING_LAYER_DIR="$ACTIVE_LAYER_ROOT_DIR/gfs013_surface_staging_${RUN}_$$"
  GFS_STAGING_PUBLIC_DIR="$ACTIVE_DATA_DIR/gfs_public_staging_${RUN}_$$"
  GFS_PUBLISH_BACKUP_DIR="$ACTIVE_DATA_DIR/gfs_publish_backup_${RUN}_$$"
  gfs_publish_ok=false
  gfs_publish_started=false

  cleanup_prior_gfs_staging() {
    rm -rf \
      "$ACTIVE_DATA_DIR"/gfs_staging_* \
      "$ACTIVE_DATA_DIR"/gfs_public_staging_* \
      "$ACTIVE_DATA_DIR"/gfs_publish_backup_* \
      "$ACTIVE_LAYER_ROOT_DIR"/gfs013_surface_staging_*
  }

  prepare_gfs_staging_data_dir() {
    rm -rf "$GFS_STAGING_DATA_DIR" "$GFS_STAGING_LAYER_DIR" "$GFS_STAGING_PUBLIC_DIR"
    mkdir -p "$GFS_STAGING_DATA_DIR" "$GFS_STAGING_LAYER_DIR" "$GFS_STAGING_PUBLIC_DIR"
    if [[ -d "$ACTIVE_DATA_DIR/copernicus_dem90" ]]; then
      cp -al "$ACTIVE_DATA_DIR/copernicus_dem90" "$GFS_STAGING_DATA_DIR/"
    fi
  }

  publish_public_link() {
    local target="$1"
    local link="$2"
    local tmp_link="${link}.tmp.$$"
    rm -f "$tmp_link"
    ln -s "$target" "$tmp_link"
    if [[ -e "$link" && ! -L "$link" ]]; then
      printf 'Refusing to replace non-symlink public path: %s\n' "$link" >&2
      rm -f "$tmp_link"
      exit 3
    fi
    mv -Tf "$tmp_link" "$link"
  }

  backup_active_path() {
    local source="$1"
    local target="$2"
    if [[ -e "$source" ]]; then
      mkdir -p "$(dirname "$target")"
      mv "$source" "$target"
    fi
  }

  restore_gfs_publish_backup() {
    local domain
    if [[ "$gfs_publish_started" != "true" || ! -d "$GFS_PUBLISH_BACKUP_DIR" ]]; then
      return
    fi
    for domain in ncep_gfs013 ncep_gfs025; do
      rm -rf "$ACTIVE_DATA_DIR/$domain" "$ACTIVE_DATA_DIR/data_run/$domain"
      if [[ -e "$GFS_PUBLISH_BACKUP_DIR/$domain" ]]; then
        mv "$GFS_PUBLISH_BACKUP_DIR/$domain" "$ACTIVE_DATA_DIR/$domain"
      fi
      if [[ -e "$GFS_PUBLISH_BACKUP_DIR/data_run/$domain" ]]; then
        mkdir -p "$ACTIVE_DATA_DIR/data_run"
        mv "$GFS_PUBLISH_BACKUP_DIR/data_run/$domain" "$ACTIVE_DATA_DIR/data_run/$domain"
      fi
    done
    rm -rf "$GFS_OUTPUT_DIR"
    if [[ -e "$GFS_PUBLISH_BACKUP_DIR/gfs013_surface" ]]; then
      mv "$GFS_PUBLISH_BACKUP_DIR/gfs013_surface" "$GFS_OUTPUT_DIR"
    fi
  }

  publish_gfs_products() {
    local domain
    for domain in ncep_gfs013 ncep_gfs025; do
      [[ -d "$GFS_STAGING_DATA_DIR/$domain" ]]
      [[ -d "$GFS_STAGING_DATA_DIR/data_run/$domain" ]]
    done
    [[ -d "$GFS_STAGING_LAYER_DIR" ]]

    gfs_publish_started=true
    rm -rf "$GFS_PUBLISH_BACKUP_DIR"
    mkdir -p "$GFS_PUBLISH_BACKUP_DIR/data_run"
    for domain in ncep_gfs013 ncep_gfs025; do
      backup_active_path "$ACTIVE_DATA_DIR/$domain" "$GFS_PUBLISH_BACKUP_DIR/$domain"
      backup_active_path "$ACTIVE_DATA_DIR/data_run/$domain" "$GFS_PUBLISH_BACKUP_DIR/data_run/$domain"
    done
    backup_active_path "$GFS_OUTPUT_DIR" "$GFS_PUBLISH_BACKUP_DIR/gfs013_surface"

    mkdir -p "$ACTIVE_DATA_DIR/data_run" "$ACTIVE_LAYER_ROOT_DIR" "$ACTIVE_PUBLIC_DATA_DIR/openmeteo_layers"
    for domain in ncep_gfs013 ncep_gfs025; do
      mv "$GFS_STAGING_DATA_DIR/$domain" "$ACTIVE_DATA_DIR/$domain"
      mv "$GFS_STAGING_DATA_DIR/data_run/$domain" "$ACTIVE_DATA_DIR/data_run/$domain"
    done
    mv "$GFS_STAGING_LAYER_DIR" "$GFS_OUTPUT_DIR"
    cp -f "$APP_DIR/config/weather_layer_catalog.json" "$ACTIVE_PUBLIC_DATA_DIR/openmeteo_layers/weather_layer_catalog.json"
    publish_public_link "$GFS_OUTPUT_DIR" "$ACTIVE_PUBLIC_DATA_DIR/openmeteo_layers/gfs013_surface"
    rm -rf "$GFS_PUBLISH_BACKUP_DIR"
    gfs_publish_started=false
  }

  on_gfs_production_exit() {
    local status="$?"
    if [[ "$status" -ne 0 ]]; then
      restore_gfs_publish_backup
    else
      rm -rf "$GFS_STAGING_DATA_DIR" "$GFS_STAGING_LAYER_DIR" "$GFS_STAGING_PUBLIC_DIR"
    fi
    exit "$status"
  }
  trap on_gfs_production_exit EXIT

  cleanup_prior_gfs_staging
  prepare_gfs_staging_data_dir
  export WEATHER_OPENMETEO_DATA_DIR="$GFS_STAGING_DATA_DIR"
  export WEATHER_OPENMETEO_LAYER_DIR="$GFS_STAGING_LAYER_DIR"
  export WEATHER_OPENMETEO_PUBLIC_DATA_DIR="$GFS_STAGING_PUBLIC_DIR"
  export WEATHER_OPENMETEO_LAYER_ROOT_DIR="$ACTIVE_LAYER_ROOT_DIR"

  download_start="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$download_start [OPENMETEO_GFS] download runtime data run=$RUN start=$download_start"
  bash scripts/download_openmeteo_gfs_data.sh
  download_end="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$download_end [OPENMETEO_GFS] download runtime data run=$RUN end=$download_end"

  python3 scripts/validate_openmeteo_latest_run.py \
    --data-dir "$GFS_STAGING_DATA_DIR" \
    --run "$RUN" \
    --domains ncep_gfs013,ncep_gfs025 \
    --min-frames "$(( ${WEATHER_GFS_MAX_FORECAST_HOUR:-120} + 1 ))" \
    --required-gfs-pressure-domain ncep_gfs025 \
    --required-gfs-pressure-levels "$GFS_UPPER_LEVELS" \
    --required-gfs-pressure-variables "$GFS_UPPER_LEVEL_VARIABLES"

  layer_start="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$layer_start [OPENMETEO_GFS] build GFS layer products start=$layer_start"
  bash scripts/build_openmeteo_gfs_layers.sh
  layer_end="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "$layer_end [OPENMETEO_GFS] build GFS layer products end=$layer_end"

  publish_gfs_products
  gfs_publish_ok=true
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_GFS] completed run=$RUN"
} 9>"$LOCK_FILE" >> "$LOG_DIR/openmeteo_gfs_cycle.log" 2>&1
