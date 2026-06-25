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

# gfs_global in Open-Meteo mixes gfs013 with gfs025. gfs013 supplies the
# high-resolution surface fields; gfs025 supplies variables absent from
# sfluxgrbf, including visibility, gusts, CAPE, lifted index, CIN, freezing
# rain flags and other weather-code dependencies. A separate gfs025 upper-level
# pass is required for the pressure-level variables exposed by /v1/forecast.
run_openmeteo download-gfs gfs013 \
  --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
  --concurrent "$GFS_CONCURRENT"

run_openmeteo download-gfs gfs025 \
  --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
  --concurrent "$GFS_CONCURRENT"

run_openmeteo download-gfs gfs025 \
  --upper-level \
  --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
  --concurrent "$GFS_CONCURRENT"

if [[ -n "${WEATHER_CAMS_FTP_USER:-}" && -n "${WEATHER_CAMS_FTP_PASSWORD:-}" ]]; then
  run_openmeteo download-cams cams_global \
    --ftpuser "$WEATHER_CAMS_FTP_USER" \
    --ftppassword "$WEATHER_CAMS_FTP_PASSWORD" \
    --concurrent "$CAMS_CONCURRENT"
else
  printf '%s\n' "Skipping CAMS global download: WEATHER_CAMS_FTP_USER/WEATHER_CAMS_FTP_PASSWORD are not set."
fi
