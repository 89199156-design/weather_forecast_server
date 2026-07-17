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
GFS_SKIP_GFS025_UPPER_LEVELS="${WEATHER_GFS_SKIP_GFS025_UPPER_LEVELS:-false}"
GFS_PRESERVE_HTTP_CACHE="${WEATHER_GFS_PRESERVE_HTTP_CACHE:-false}"
if ! is_truthy "$GFS_PRESERVE_HTTP_CACHE"; then
  cleanup_openmeteo_http_cache
fi
prepare_openmeteo_http_cache

GFS_MAX_FORECAST_HOUR="${WEATHER_GFS_MAX_FORECAST_HOUR:-384}"
GFS_CONCURRENT="${WEATHER_GFS_DOWNLOAD_CONCURRENT:-4}"
GFS013_SURFACE_VARIABLES="${WEATHER_GFS013_SURFACE_VARIABLES:-temperature_2m,surface_temperature,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,pressure_msl,relative_humidity_2m,precipitation,wind_v_component_10m,wind_u_component_10m,snow_depth,showers,frozen_precipitation_percent,uv_index,uv_index_clear_sky,boundary_layer_height,shortwave_radiation,latent_heat_flux,sensible_heat_flux,diffuse_radiation,total_column_integrated_water_vapour,soil_temperature_0_to_10cm,soil_temperature_10_to_40cm,soil_temperature_40_to_100cm,soil_temperature_100_to_200cm,soil_moisture_0_to_10cm,soil_moisture_10_to_40cm,soil_moisture_40_to_100cm,soil_moisture_100_to_200cm}"
GFS025_SURFACE_VARIABLES="${WEATHER_GFS025_SURFACE_VARIABLES:-pressure_msl,categorical_freezing_rain,temperature_80m,temperature_100m,wind_v_component_80m,wind_u_component_80m,wind_v_component_100m,wind_u_component_100m,wind_gusts_10m,freezing_level_height,cape,lifted_index,convective_inhibition,visibility}"

ensure_csv_variable() {
  local list_name="$1"
  local variable="$2"
  local current="${!list_name}"
  case ",$current," in
    *,"$variable",*) ;;
    *) printf -v "$list_name" '%s' "${current},${variable}" ;;
  esac
}

# Older private environments may still carry the former WebP-only allowlists.
# Always restore every official GFS input required by the public API while
# retaining explicitly configured ordering and any additional variables.
for variable in \
  temperature_2m surface_temperature cloud_cover cloud_cover_low cloud_cover_mid \
  cloud_cover_high pressure_msl relative_humidity_2m precipitation \
  wind_v_component_10m wind_u_component_10m snow_depth showers \
  frozen_precipitation_percent uv_index uv_index_clear_sky boundary_layer_height \
  shortwave_radiation latent_heat_flux sensible_heat_flux diffuse_radiation \
  total_column_integrated_water_vapour soil_temperature_0_to_10cm \
  soil_temperature_10_to_40cm soil_temperature_40_to_100cm \
  soil_temperature_100_to_200cm soil_moisture_0_to_10cm \
  soil_moisture_10_to_40cm soil_moisture_40_to_100cm \
  soil_moisture_100_to_200cm; do
  ensure_csv_variable GFS013_SURFACE_VARIABLES "$variable"
done
for variable in \
  pressure_msl categorical_freezing_rain temperature_80m temperature_100m \
  wind_v_component_80m wind_u_component_80m wind_v_component_100m \
  wind_u_component_100m wind_gusts_10m freezing_level_height cape lifted_index \
  convective_inhibition visibility; do
  ensure_csv_variable GFS025_SURFACE_VARIABLES "$variable"
done
GFS_UPPER_LEVELS="${WEATHER_GFS_UPPER_LEVELS:-1000,975,950,925,900,850,800,750,700,650,600,550,500,450,400,350,300,250,200,150,100,50}"
GFS_UPPER_LEVEL_VARIABLES="${WEATHER_GFS_UPPER_LEVEL_VARIABLES:-temperature,wind_u_component,wind_v_component,geopotential_height,cloud_cover,relative_humidity,vertical_velocity}"
GFS_UPPER_LEVEL_CONCURRENT="${WEATHER_GFS_UPPER_LEVEL_DOWNLOAD_CONCURRENT:-4}"
GFS_UPPER_LEVEL_BATCH_SIZE="${WEATHER_GFS_UPPER_LEVEL_BATCH_SIZE:-4}"
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
  local requested_levels="${1:-$GFS_UPPER_LEVELS}"
  local IFS=","
  local family
  local level
  local levels=()
  local variables=()
  local only_variables=()

  read -ra levels <<< "$requested_levels"
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
  local start
  local batch_levels
  local levels=()

  if ! [[ "$GFS_UPPER_LEVEL_BATCH_SIZE" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "WEATHER_GFS_UPPER_LEVEL_BATCH_SIZE must be a positive integer" >&2
    exit 2
  fi
  IFS=',' read -ra levels <<< "$GFS_UPPER_LEVELS"

  for ((start = 0; start < ${#levels[@]}; start += GFS_UPPER_LEVEL_BATCH_SIZE)); do
    batch_levels="$(join_by_comma "${levels[@]:start:GFS_UPPER_LEVEL_BATCH_SIZE}")"
    GFS025_UPPER_LEVEL_ONLY_VARIABLES="$(gfs025_upper_level_only_variables "$batch_levels")"
    if [[ -z "$GFS025_UPPER_LEVEL_ONLY_VARIABLES" ]]; then
      continue
    fi

    echo "Downloading GFS upper-level batch levels=$batch_levels run=$GFS_RUN"
    run_openmeteo download-gfs gfs025 \
      "${GFS_DOWNLOAD_SOURCE_ARGS[@]}" \
      --only-variables "$GFS025_UPPER_LEVEL_ONLY_VARIABLES" \
      $(append_run_arg "$GFS_RUN") \
      --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
      --concurrent "$GFS_UPPER_LEVEL_CONCURRENT"

    cleanup_download_work_dirs "$DATA_DIR/download-ncep_gfs025"
    cleanup_openmeteo_http_cache
  done
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

  if is_truthy "$GFS_SKIP_GFS025_UPPER_LEVELS"; then
    echo "Skipping unchanged GFS upper-level variables for repair run=$GFS_RUN"
  else
    download_gfs025_upper_level_variables
  fi
fi

cleanup_download_work_dirs \
  "$DATA_DIR/download-ncep_gfs013" \
  "$DATA_DIR/download-ncep_gfs025"
cleanup_openmeteo_http_cache
