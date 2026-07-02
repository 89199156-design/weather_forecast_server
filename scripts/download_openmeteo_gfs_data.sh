#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/openmeteo_runtime_common.sh"

load_weather_env
openmeteo_set_runtime_defaults
write_sanitized_env_file
cleanup_sanitized_env() {
  rm -f "$SANITIZED_ENV_FILE"
}
trap cleanup_sanitized_env EXIT

GFS_MAX_FORECAST_HOUR="${WEATHER_GFS_MAX_FORECAST_HOUR:-120}"
GFS_CONCURRENT="${WEATHER_GFS_DOWNLOAD_CONCURRENT:-4}"
GFS_UPPER_LEVELS="${WEATHER_GFS_UPPER_LEVELS:-1000,975,950,925,900,850,800,750,700,650,600,550,500,400,300,200}"
GFS_UPPER_LEVEL_PGRB2_LEVELS="${WEATHER_GFS_UPPER_LEVEL_PGRB2_LEVELS:-1000,975,950,925,900,850,800,750,700,650,600,550,500,400,300,200}"
GFS_UPPER_LEVEL_VARIABLES="${WEATHER_GFS_UPPER_LEVEL_VARIABLES:-temperature,wind_u_component,wind_v_component,geopotential_height,cloud_cover,relative_humidity,vertical_velocity}"
GFS_UPPER_LEVEL_CHUNK_SIZE="${WEATHER_GFS_UPPER_LEVEL_CHUNK_SIZE:-4}"
GFS_UPPER_LEVEL_CONCURRENT="${WEATHER_GFS_UPPER_LEVEL_DOWNLOAD_CONCURRENT:-1}"
GFS013_RUN="${WEATHER_GFS013_RUN:-${WEATHER_GFS_RUN:-}}"
GFS025_RUN="${WEATHER_GFS025_RUN:-${WEATHER_GFS_RUN:-}}"

level_is_in_csv() {
  local needle="$1"
  local csv="$2"
  local IFS=","
  local level
  local levels=()

  read -ra levels <<< "$csv"
  for level in "${levels[@]}"; do
    level="${level//[[:space:]]/}"
    if [[ "$level" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}

emit_upper_level_only_variable_chunks() {
  local variable="$1"
  shift
  local chunk=()
  local level
  local chunk_size="$GFS_UPPER_LEVEL_CHUNK_SIZE"

  if ! [[ "$chunk_size" =~ ^[0-9]+$ ]] || [[ "$chunk_size" -lt 1 ]]; then
    printf '%s\n' "WEATHER_GFS_UPPER_LEVEL_CHUNK_SIZE must be a positive integer." >&2
    exit 2
  fi

  for level in "$@"; do
    chunk+=("${variable}_${level}hPa")
    if [[ "${#chunk[@]}" -ge "$chunk_size" ]]; then
      printf '%s\n' "${chunk[*]}"
      chunk=()
    fi
  done

  if [[ "${#chunk[@]}" -gt 0 ]]; then
    printf '%s\n' "${chunk[*]}"
  fi
}

upper_level_only_variable_chunks() {
  local variable="$1"
  local IFS=","
  local level
  local levels=()
  local primary_levels=()
  local secondary_levels=()

  read -ra levels <<< "$GFS_UPPER_LEVELS"
  for level in "${levels[@]}"; do
    level="${level//[[:space:]]/}"
    if [[ -z "$level" ]]; then
      continue
    fi
    if [[ "$variable" == "cloud_cover" && ( "$level" -lt 50 || "$level" == "70" ) ]]; then
      continue
    fi

    if level_is_in_csv "$level" "$GFS_UPPER_LEVEL_PGRB2_LEVELS"; then
      primary_levels+=("$level")
    else
      secondary_levels+=("$level")
    fi
  done

  emit_upper_level_only_variable_chunks "$variable" "${primary_levels[@]}"
  emit_upper_level_only_variable_chunks "$variable" "${secondary_levels[@]}"
}

download_gfs025_upper_level_variable() {
  local variable="$1"
  local only_variables

  while IFS= read -r only_variables; do
    if [[ -z "$only_variables" ]]; then
      continue
    fi

    run_openmeteo download-gfs gfs025 \
      --only-variables "$only_variables" \
      $(append_run_arg "$GFS025_RUN") \
      --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
      --concurrent "$GFS_UPPER_LEVEL_CONCURRENT"
  done < <(upper_level_only_variable_chunks "$variable")
}

require_dem_source
cleanup_download_work_dirs \
  "$DATA_DIR/download-ncep_gfs013" \
  "$DATA_DIR/download-ncep_gfs025"

run_openmeteo download-gfs gfs013 \
  $(append_run_arg "$GFS013_RUN") \
  --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
  --concurrent "$GFS_CONCURRENT"

run_openmeteo download-gfs gfs025 \
  $(append_run_arg "$GFS025_RUN") \
  --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
  --concurrent "$GFS_CONCURRENT"

IFS="," read -ra upper_variables <<< "$GFS_UPPER_LEVEL_VARIABLES"
for variable in "${upper_variables[@]}"; do
  variable="${variable//[[:space:]]/}"
  if [[ -z "$variable" ]]; then
    continue
  fi
  download_gfs025_upper_level_variable "$variable"
done

cleanup_openmeteo_http_cache

