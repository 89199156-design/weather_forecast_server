#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/openmeteo_runtime_common.sh"

load_weather_env
WEATHER_OPENMETEO_HTTP_CACHE_DIR="/app/data/http_cache/gfs"
export WEATHER_OPENMETEO_HTTP_CACHE_DIR
HTTP_CACHE="/app/data/http_cache/gfs"
export HTTP_CACHE
openmeteo_set_runtime_defaults
write_sanitized_env_file
cleanup_sensitive_artifacts() {
  rm -f "${SANITIZED_ENV_FILE:-}"
}
trap cleanup_sensitive_artifacts EXIT

GFS_SKIP_GFS013="${WEATHER_GFS_SKIP_GFS013:-false}"
GFS_SKIP_GFS025="${WEATHER_GFS_SKIP_GFS025:-false}"
GFS_PRESERVE_HTTP_CACHE="${WEATHER_GFS_PRESERVE_HTTP_CACHE:-false}"
if ! is_truthy "$GFS_PRESERVE_HTTP_CACHE"; then
  cleanup_openmeteo_http_cache
fi
prepare_openmeteo_http_cache

GFS_MAX_FORECAST_HOUR="${WEATHER_GFS_MAX_FORECAST_HOUR:-384}"
GFS_CONCURRENT="${WEATHER_GFS_DOWNLOAD_CONCURRENT:-4}"
GFS013_SURFACE_VARIABLES="${WEATHER_GFS013_SURFACE_VARIABLES:-temperature_2m,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,pressure_msl,relative_humidity_2m,precipitation,wind_v_component_10m,wind_u_component_10m,snow_depth,showers,frozen_precipitation_percent,uv_index,uv_index_clear_sky,boundary_layer_height,shortwave_radiation,latent_heat_flux}"
# The public API exposes uv_index_clear_sky and its 3-day maximum. Keep this
# official GFS CDUVB field mandatory even when an older deployed env override
# still contains the pre-migration surface allowlist.
case ",$GFS013_SURFACE_VARIABLES," in
  *,uv_index_clear_sky,*) ;;
  *) GFS013_SURFACE_VARIABLES="${GFS013_SURFACE_VARIABLES},uv_index_clear_sky" ;;
esac
GFS025_SURFACE_VARIABLES="${WEATHER_GFS025_SURFACE_VARIABLES:-pressure_msl,categorical_freezing_rain,wind_gusts_10m,cape,lifted_index,convective_inhibition,visibility,latent_heat_flux}"
GFS_UPPER_LEVELS="${WEATHER_GFS_UPPER_LEVELS:-1000,975,950,925,900,850,800,750,700,650,600,550,500,450,400,350,300,250,200,150,100,50}"
GFS_UPPER_LEVEL_VARIABLES="${WEATHER_GFS_UPPER_LEVEL_VARIABLES:-temperature,wind_u_component,wind_v_component,geopotential_height,cloud_cover,relative_humidity,vertical_velocity}"
GFS_UPPER_LEVEL_CONCURRENT="${WEATHER_GFS_UPPER_LEVEL_DOWNLOAD_CONCURRENT:-4}"
GFS_RUN="${WEATHER_GFS_RUN:-}"
GFS_DOWNLOAD_MODE="${WEATHER_GFS_DOWNLOAD_MODE:-s3-range-region}"
GFS_DOWNLOAD_SOURCE_ARGS=()
if [[ "$GFS_DOWNLOAD_MODE" == "s3-range-region" || "$GFS_DOWNLOAD_MODE" == "aws-global" ]]; then
  GFS_DOWNLOAD_SOURCE_ARGS=(--download-from-aws)
elif [[ "$GFS_DOWNLOAD_MODE" != "nomads-region" ]]; then
  printf '%s\n' "Unsupported WEATHER_GFS_DOWNLOAD_MODE: $GFS_DOWNLOAD_MODE" >&2
  exit 2
fi

join_by_comma() {
  local IFS=","
  printf '%s\n' "$*"
}

gfs025_upper_level_only_variables() {
  local IFS=","
  local family
  local level
  local levels=()
  local variables=()
  local only_variables=()

  read -ra levels <<< "$GFS_UPPER_LEVELS"
  read -ra variables <<< "$GFS_UPPER_LEVEL_VARIABLES"

  for family in "${variables[@]}"; do
    family="${family//[[:space:]]/}"
    if [[ -z "$family" ]]; then
      continue
    fi
    for level in "${levels[@]}"; do
      level="${level//[[:space:]]/}"
      if [[ -z "$level" ]]; then
        continue
      fi
      if [[ "$family" == "cloud_cover" && ( "$level" -lt 50 || "$level" == "70" ) ]]; then
        continue
      fi

      only_variables+=("${family}_${level}hPa")
    done
  done

  join_by_comma "${only_variables[@]}"
}

download_gfs025_upper_level_variables() {
  local GFS025_UPPER_LEVEL_ONLY_VARIABLES

  GFS025_UPPER_LEVEL_ONLY_VARIABLES="$(gfs025_upper_level_only_variables)"
  if [[ -z "$GFS025_UPPER_LEVEL_ONLY_VARIABLES" ]]; then
    return
  fi

  run_openmeteo download-gfs gfs025 \
    "${GFS_DOWNLOAD_SOURCE_ARGS[@]}" \
    --only-variables "$GFS025_UPPER_LEVEL_ONLY_VARIABLES" \
    $(append_run_arg "$GFS_RUN") \
    --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
    --concurrent "$GFS_UPPER_LEVEL_CONCURRENT"

  cleanup_download_work_dirs "$DATA_DIR/download-ncep_gfs025"
  cleanup_openmeteo_http_cache
}

require_dem_source
cleanup_download_work_dirs \
  "$DATA_DIR/download-ncep_gfs013" \
  "$DATA_DIR/download-ncep_gfs025"

if is_truthy "$GFS_SKIP_GFS013"; then
  echo "Skipping validated gfs013 component for resumed run=$GFS_RUN"
else
  run_openmeteo download-gfs gfs013 \
    "${GFS_DOWNLOAD_SOURCE_ARGS[@]}" \
    --only-variables "$GFS013_SURFACE_VARIABLES" \
    $(append_run_arg "$GFS_RUN") \
    --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
    --concurrent "$GFS_CONCURRENT"
  cleanup_download_work_dirs "$DATA_DIR/download-ncep_gfs013"
  cleanup_openmeteo_http_cache
fi

if is_truthy "$GFS_SKIP_GFS025"; then
  echo "Skipping unchanged gfs025 component for repair run=$GFS_RUN"
else
  run_openmeteo download-gfs gfs025 \
    "${GFS_DOWNLOAD_SOURCE_ARGS[@]}" \
    --only-variables "$GFS025_SURFACE_VARIABLES" \
    $(append_run_arg "$GFS_RUN") \
    --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
    --concurrent "$GFS_CONCURRENT"
  cleanup_download_work_dirs "$DATA_DIR/download-ncep_gfs025"
  cleanup_openmeteo_http_cache

  download_gfs025_upper_level_variables
fi

cleanup_download_work_dirs \
  "$DATA_DIR/download-ncep_gfs013" \
  "$DATA_DIR/download-ncep_gfs025"
cleanup_openmeteo_http_cache
