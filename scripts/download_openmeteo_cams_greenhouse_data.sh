#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/openmeteo_runtime_common.sh"

read_cdsapi_key() {
  local candidate
  for candidate in \
    "${WEATHER_CAMS_CDSAPI_RC:-}" \
    "${CDSAPI_RC:-}" \
    "${HOME:-}/.cdsapirc" \
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

WEATHER_OPENMETEO_HTTP_CACHE_DIR="/app/data/http_cache/cams_greenhouse"
export WEATHER_OPENMETEO_HTTP_CACHE_DIR
HTTP_CACHE="/app/data/http_cache/cams_greenhouse"
export HTTP_CACHE
openmeteo_set_runtime_defaults
write_sanitized_env_file
cleanup_sensitive_artifacts() {
  rm -f "${SANITIZED_ENV_FILE:-}"
}
trap cleanup_sensitive_artifacts EXIT

cleanup_openmeteo_http_cache
prepare_openmeteo_http_cache

CAMS_ADS_KEY="${WEATHER_CAMS_ADS_KEY:-${WEATHER_CAMS_CDS_KEY:-}}"
CAMS_CONCURRENT="${WEATHER_CAMS_GREENHOUSE_DOWNLOAD_CONCURRENT:-1}"
CAMS_GREENHOUSE_VARIABLES="${WEATHER_CAMS_GREENHOUSE_VARIABLES:-carbon_monoxide}"
CAMS_GREENHOUSE_RUN="${WEATHER_CAMS_GREENHOUSE_RUN:-}"

if [[ -z "$CAMS_ADS_KEY" ]]; then
  printf '%s\n' "WEATHER_CAMS_ADS_KEY/WEATHER_CAMS_CDS_KEY or a readable .cdsapirc is required for CAMS greenhouse ADS download." >&2
  exit 2
fi
if [[ -z "$CAMS_GREENHOUSE_RUN" ]]; then
  printf '%s\n' "WEATHER_CAMS_GREENHOUSE_RUN is required." >&2
  exit 2
fi

cleanup_download_work_dirs "$DATA_DIR/download-cams_global_greenhouse_gases"
run_openmeteo download-cams cams_global_greenhouse_gases \
  --run "$CAMS_GREENHOUSE_RUN" \
  --only-variables "$CAMS_GREENHOUSE_VARIABLES" \
  --concurrent "$CAMS_CONCURRENT"

cleanup_download_work_dirs "$DATA_DIR/download-cams_global_greenhouse_gases"
cleanup_openmeteo_http_cache
