#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/openmeteo_runtime_common.sh"

read_cdsapi_key() {
  local candidate
  for candidate in \
    "${WEATHER_CAMS_CDSAPI_RC:-}" \
    "${CDSAPI_RC:-}" \
    "$HOME/.cdsapirc" \
    "/home/ubuntu/.cdsapirc" \
    "/root/.cdsapirc"; do
    if [[ -z "$candidate" || ! -r "$candidate" ]]; then
      continue
    fi
    awk '
      $0 ~ /^[[:space:]]*key[[:space:]]*:/ {
        sub(/^[[:space:]]*key[[:space:]]*:/, "", $0)
        sub(/^[[:space:]]+/, "", $0)
        sub(/[[:space:]]+$/, "", $0)
        print $0
        exit
      }
    ' "$candidate"
    return
  done
}

load_weather_env
if [[ -z "${WEATHER_CAMS_ADS_KEY:-}" && -z "${WEATHER_CAMS_CDS_KEY:-}" ]]; then
  WEATHER_CAMS_ADS_KEY="$(read_cdsapi_key)"
  export WEATHER_CAMS_ADS_KEY
fi
WEATHER_OPENMETEO_HTTP_CACHE_DIR="/app/data/http_cache/cams_ads"
export WEATHER_OPENMETEO_HTTP_CACHE_DIR
HTTP_CACHE="/app/data/http_cache/cams_ads"
export HTTP_CACHE
openmeteo_set_runtime_defaults
write_sanitized_env_file
cleanup_sensitive_artifacts() {
  rm -f "${SANITIZED_ENV_FILE:-}"
}
trap cleanup_sensitive_artifacts EXIT

cleanup_openmeteo_http_cache

CAMS_CONCURRENT="${WEATHER_CAMS_DOWNLOAD_CONCURRENT:-1}"
CAMS_ADS_KEY="${WEATHER_CAMS_ADS_KEY:-${WEATHER_CAMS_CDS_KEY:-}}"
CAMS_VARIABLES="${WEATHER_CAMS_VARIABLES:-pm2_5,pm10,aerosol_optical_depth,dust,carbon_monoxide,nitrogen_dioxide,ozone,sulphur_dioxide}"
CAMS_RUN="${WEATHER_CAMS_RUN:-}"

if [[ -z "$CAMS_ADS_KEY" ]]; then
  printf '%s\n' "WEATHER_CAMS_ADS_KEY/WEATHER_CAMS_CDS_KEY are required for CAMS ADS/CDS download." >&2
  exit 2
fi

cleanup_download_work_dirs "$DATA_DIR/download-cams_global"
run_openmeteo download-cams-ads cams_global \
  $(append_run_arg "$CAMS_RUN") \
  --cdskey "$CAMS_ADS_KEY" \
  --only-variables "$CAMS_VARIABLES" \
  --concurrent "$CAMS_CONCURRENT"

cleanup_openmeteo_http_cache
