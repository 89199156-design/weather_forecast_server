#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
IMAGE_NAME="${WEATHER_OPENMETEO_IMAGE:-weather-forecast-openmeteo}"
IMAGE_TAG="${WEATHER_OPENMETEO_TAG:-latest}"
DATA_DIR="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}"
ENV_FILE="${WEATHER_OPENMETEO_ENV_FILE:-$APP_DIR/config/singapore.example.env}"
GFS_MAX_FORECAST_HOUR="${WEATHER_GFS_MAX_FORECAST_HOUR:-120}"
GFS_CONCURRENT="${WEATHER_GFS_DOWNLOAD_CONCURRENT:-4}"
CAMS_CONCURRENT="${WEATHER_CAMS_DOWNLOAD_CONCURRENT:-1}"
GFS_UPPER_LEVELS="${WEATHER_GFS_UPPER_LEVELS:-10,15,20,30,40,50,70,100,125,150,175,200,225,250,275,300,325,350,375,400,425,450,475,500,525,550,575,600,625,650,675,700,725,750,775,800,825,850,875,900,925,950,975,1000}"
GFS_UPPER_LEVEL_VARIABLES="${WEATHER_GFS_UPPER_LEVEL_VARIABLES:-temperature,wind_u_component,wind_v_component,geopotential_height,cloud_cover,relative_humidity,vertical_velocity}"

cd "$APP_DIR"
mkdir -p "$DATA_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

run_openmeteo() {
  docker run --rm \
    --env-file "$ENV_FILE" \
    --volume "$DATA_DIR:/app/data" \
    "$IMAGE_NAME:$IMAGE_TAG" \
    "$@"
}

upper_level_only_variables() {
  local variable="$1"
  local IFS=","
  local selected=()
  local level

  read -ra levels <<< "$GFS_UPPER_LEVELS"
  for level in "${levels[@]}"; do
    if [[ "$variable" == "cloud_cover" && ( "$level" -lt 50 || "$level" == "70" ) ]]; then
      continue
    fi
    selected+=("${variable}_${level}hPa")
  done

  printf '%s' "${selected[*]}"
}

download_gfs025_upper_level_variable() {
  local variable="$1"
  local only_variables

  only_variables="$(upper_level_only_variables "$variable")"
  if [[ -z "$only_variables" ]]; then
    return
  fi

  run_openmeteo download-gfs gfs025 \
    --only-variables "$only_variables" \
    --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
    --concurrent "$GFS_CONCURRENT"
}

# gfs_global in Open-Meteo mixes gfs013 with gfs025. gfs013 supplies the
# high-resolution surface fields; gfs025 supplies variables absent from
# sfluxgrbf, including visibility, gusts, CAPE, lifted index, CIN, freezing
# rain flags and other weather-code dependencies. Pressure-level variables are
# downloaded in smaller Open-Meteo only-variables batches to keep NOAA filter
# requests stable while preserving Open-Meteo decoding and conversion.
run_openmeteo download-gfs gfs013 \
  --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
  --concurrent "$GFS_CONCURRENT"

run_openmeteo download-gfs gfs025 \
  --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
  --concurrent "$GFS_CONCURRENT"

IFS="," read -ra upper_variables <<< "$GFS_UPPER_LEVEL_VARIABLES"
for variable in "${upper_variables[@]}"; do
  download_gfs025_upper_level_variable "$variable"
done

if [[ -n "${WEATHER_CAMS_FTP_USER:-}" && -n "${WEATHER_CAMS_FTP_PASSWORD:-}" ]]; then
  run_openmeteo download-cams cams_global \
    --ftpuser "$WEATHER_CAMS_FTP_USER" \
    --ftppassword "$WEATHER_CAMS_FTP_PASSWORD" \
    --concurrent "$CAMS_CONCURRENT"
else
  printf '%s\n' "Skipping CAMS global download: WEATHER_CAMS_FTP_USER/WEATHER_CAMS_FTP_PASSWORD are not set."
fi
