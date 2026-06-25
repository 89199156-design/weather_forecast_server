#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
ENV_FILE="${WEATHER_OPENMETEO_ENV_FILE:-$APP_DIR/config/singapore.example.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

IMAGE_NAME="${WEATHER_OPENMETEO_IMAGE:-weather-forecast-openmeteo}"
IMAGE_TAG="${WEATHER_OPENMETEO_TAG:-latest}"
DATA_DIR="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}"
GFS_MAX_FORECAST_HOUR="${WEATHER_GFS_MAX_FORECAST_HOUR:-120}"
GFS_CONCURRENT="${WEATHER_GFS_DOWNLOAD_CONCURRENT:-4}"
CAMS_CONCURRENT="${WEATHER_CAMS_DOWNLOAD_CONCURRENT:-1}"
GFS_UPPER_LEVELS="${WEATHER_GFS_UPPER_LEVELS:-10,15,20,30,40,50,70,100,125,150,175,200,225,250,275,300,325,350,375,400,425,450,475,500,525,550,575,600,625,650,675,700,725,750,775,800,825,850,875,900,925,950,975,1000}"
GFS_UPPER_LEVEL_PGRB2_LEVELS="${WEATHER_GFS_UPPER_LEVEL_PGRB2_LEVELS:-10,15,20,30,40,50,70,100,150,200,250,300,350,400,450,500,550,600,650,700,750,800,850,900,925,950,975,1000}"
GFS_UPPER_LEVEL_VARIABLES="${WEATHER_GFS_UPPER_LEVEL_VARIABLES:-temperature,wind_u_component,wind_v_component,geopotential_height,cloud_cover,relative_humidity,vertical_velocity}"
GFS_UPPER_LEVEL_CHUNK_SIZE="${WEATHER_GFS_UPPER_LEVEL_CHUNK_SIZE:-4}"
GFS_UPPER_LEVEL_CONCURRENT="${WEATHER_GFS_UPPER_LEVEL_DOWNLOAD_CONCURRENT:-1}"
SKIP_GFS013_DOWNLOAD="${WEATHER_SKIP_GFS013_DOWNLOAD:-false}"
SKIP_GFS025_SURFACE_DOWNLOAD="${WEATHER_SKIP_GFS025_SURFACE_DOWNLOAD:-false}"
SKIP_GFS025_UPPER_LEVEL_DOWNLOAD="${WEATHER_SKIP_GFS025_UPPER_LEVEL_DOWNLOAD:-false}"
SKIP_CAMS_DOWNLOAD="${WEATHER_SKIP_CAMS_DOWNLOAD:-false}"

cd "$APP_DIR"
mkdir -p "$DATA_DIR"

is_truthy() {
  local value="${1:-}"
  value="${value,,}"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" || "$value" == "on" ]]
}

run_openmeteo() {
  docker run --rm \
    --env-file "$ENV_FILE" \
    --volume "$DATA_DIR:/app/data" \
    "$IMAGE_NAME:$IMAGE_TAG" \
    "$@"
}

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
  local IFS=","
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
      --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
      --concurrent "$GFS_UPPER_LEVEL_CONCURRENT"
  done < <(upper_level_only_variable_chunks "$variable")
}

# gfs_global in Open-Meteo mixes gfs013 with gfs025. gfs013 supplies the
# high-resolution surface fields; gfs025 supplies variables absent from
# sfluxgrbf, including visibility, gusts, CAPE, lifted index, CIN, freezing
# rain flags and other weather-code dependencies. Pressure-level variables are
# downloaded in smaller Open-Meteo only-variables batches to keep NOAA filter
# requests stable while preserving Open-Meteo decoding and conversion.
if is_truthy "$SKIP_GFS013_DOWNLOAD"; then
  printf '%s\n' "Skipping GFS013 download: WEATHER_SKIP_GFS013_DOWNLOAD is enabled."
else
  run_openmeteo download-gfs gfs013 \
    --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
    --concurrent "$GFS_CONCURRENT"
fi

if is_truthy "$SKIP_GFS025_SURFACE_DOWNLOAD"; then
  printf '%s\n' "Skipping GFS025 surface download: WEATHER_SKIP_GFS025_SURFACE_DOWNLOAD is enabled."
else
  run_openmeteo download-gfs gfs025 \
    --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
    --concurrent "$GFS_CONCURRENT"
fi

if is_truthy "$SKIP_GFS025_UPPER_LEVEL_DOWNLOAD"; then
  printf '%s\n' "Skipping GFS025 pressure-level download: WEATHER_SKIP_GFS025_UPPER_LEVEL_DOWNLOAD is enabled."
else
  IFS="," read -ra upper_variables <<< "$GFS_UPPER_LEVEL_VARIABLES"
  for variable in "${upper_variables[@]}"; do
    variable="${variable//[[:space:]]/}"
    if [[ -z "$variable" ]]; then
      continue
    fi
    download_gfs025_upper_level_variable "$variable"
  done
fi

if is_truthy "$SKIP_CAMS_DOWNLOAD"; then
  printf '%s\n' "Skipping CAMS global download: WEATHER_SKIP_CAMS_DOWNLOAD is enabled."
elif [[ -n "${WEATHER_CAMS_FTP_USER:-}" && -n "${WEATHER_CAMS_FTP_PASSWORD:-}" ]]; then
  run_openmeteo download-cams cams_global \
    --ftpuser "$WEATHER_CAMS_FTP_USER" \
    --ftppassword "$WEATHER_CAMS_FTP_PASSWORD" \
    --concurrent "$CAMS_CONCURRENT"
else
  printf '%s\n' "Skipping CAMS global download: WEATHER_CAMS_FTP_USER/WEATHER_CAMS_FTP_PASSWORD are not set."
fi
