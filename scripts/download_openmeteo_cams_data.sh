#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/openmeteo_runtime_common.sh"

load_weather_env
WEATHER_OPENMETEO_HTTP_CACHE_DIR="/app/data/http_cache/cams_ftp"
export WEATHER_OPENMETEO_HTTP_CACHE_DIR
HTTP_CACHE="/app/data/http_cache/cams_ftp"
export HTTP_CACHE
openmeteo_set_runtime_defaults
write_sanitized_env_file
cleanup_sensitive_artifacts() {
  rm -f "${SANITIZED_ENV_FILE:-}"
}
trap cleanup_sensitive_artifacts EXIT

cleanup_openmeteo_http_cache
prepare_openmeteo_http_cache

CAMS_CONCURRENT="${WEATHER_CAMS_FTP_DOWNLOAD_CONCURRENT:-8}"
CAMS_FTP_USER="${WEATHER_CAMS_FTP_USER:-}"
CAMS_FTP_PASSWORD="${WEATHER_CAMS_FTP_PASSWORD:-}"
CAMS_VARIABLES="${WEATHER_CAMS_VARIABLES:-pm2_5,pm10,aerosol_optical_depth,dust,carbon_monoxide,nitrogen_dioxide,ozone,sulphur_dioxide}"
CAMS_RUN="${WEATHER_CAMS_RUN:-}"

if [[ -z "$CAMS_FTP_USER" || -z "$CAMS_FTP_PASSWORD" ]]; then
  printf '%s\n' "Both WEATHER_CAMS_FTP_USER and WEATHER_CAMS_FTP_PASSWORD are required for CAMS FTP/ECPDS download." >&2
  exit 2
fi

cleanup_download_work_dirs "$DATA_DIR/download-cams_global"
run_openmeteo download-cams cams_global \
  $(append_run_arg "$CAMS_RUN") \
  --ftpuser "$CAMS_FTP_USER" \
  --ftppassword "$CAMS_FTP_PASSWORD" \
  --only-variables "$CAMS_VARIABLES" \
  --concurrent "$CAMS_CONCURRENT"

cleanup_download_work_dirs "$DATA_DIR/download-cams_global"
cleanup_openmeteo_http_cache
