#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env
WEATHER_OPENMETEO_HTTP_CACHE_DIR="/app/data/http_cache/gfs-probability"
WEATHER_OPENMETEO_HTTP_CACHE_ENABLED="${WEATHER_GFS_HTTP_CACHE_ENABLED:-false}"
export WEATHER_OPENMETEO_HTTP_CACHE_DIR
export WEATHER_OPENMETEO_HTTP_CACHE_ENABLED
unset HTTP_CACHE
openmeteo_set_runtime_defaults
write_sanitized_env_file
cleanup_sensitive_artifacts() {
  rm -f -- "${SANITIZED_ENV_FILE:-}"
}
trap cleanup_sensitive_artifacts EXIT

RUN="${1:-${WEATHER_GFS_RUN:-}}"
if [[ ! "$RUN" =~ ^[0-9]{10}$ || ! "${RUN:8:2}" =~ ^(00|06|12|18)$ ]]; then
  printf '%s\n' "Usage: download_gfs_probability_data.sh YYYYMMDD{00|06|12|18}" >&2
  exit 2
fi

DATA_DIR="${WEATHER_OPENMETEO_DATA_DIR:?WEATHER_OPENMETEO_DATA_DIR is required}"
export WEATHER_GFS_DOWNLOAD_MODE="${WEATHER_GFS_DOWNLOAD_MODE:-nomads-region}"
if [[ "$WEATHER_GFS_DOWNLOAD_MODE" != "nomads-region" ]]; then
  printf '%s\n' "GFS probability production requires NOMADS regional download" >&2
  exit 2
fi

remove_scoped_runtime_domain() {
  local domain="$1"
  local target="$DATA_DIR/$domain"
  local data_real
  local target_real
  [[ -e "$target" ]] || return 0
  data_real="$(readlink -f -- "$DATA_DIR")"
  target_real="$(readlink -f -- "$target")"
  if [[ "$(dirname -- "$target_real")" != "$data_real" \
    || "$(basename -- "$target_real")" != "$domain" ]]; then
    printf '%s\n' "unsafe GFS probability runtime path: $target_real" >&2
    exit 1
  fi
  rm -rf -- "$target_real"
}

download_probability_domain() {
  local source_domain="$1"
  local runtime_domain="$2"
  local max_hour="$3"

  remove_scoped_runtime_domain "$runtime_domain"
  cleanup_download_work_dirs "$DATA_DIR/download-$runtime_domain"
  printf '%s\n' \
    "Downloading GFS precipitation probability domain=$source_domain run=$RUN horizon=$max_hour"
  run_openmeteo download-gfs "$source_domain" \
    --run "$RUN" \
    --only-variables precipitation \
    --max-forecast-hour "$max_hour" \
    --concurrent "${WEATHER_GFS_PROBABILITY_CONCURRENT:-2}" \
    --probability-full-run-only
  cleanup_download_work_dirs "$DATA_DIR/download-$runtime_domain"
  cleanup_openmeteo_http_cache
  remove_scoped_runtime_domain "$runtime_domain"
}

download_probability_domain gfs025_ens ncep_gefs025 240
download_probability_domain gfs05_ens ncep_gefs05 384
