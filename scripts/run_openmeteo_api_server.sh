#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"

load_weather_env
openmeteo_set_runtime_defaults
require_dem_source
write_sanitized_env_file

CONTAINER_NAME="${WEATHER_OPENMETEO_CONTAINER:-weather-forecast-openmeteo-api}"
PORT="${WEATHER_OPENMETEO_PORT:-18080}"

cleanup() {
  rm -f "${SANITIZED_ENV_FILE:-}"
}
trap cleanup EXIT

cd "$APP_DIR"
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --env-file "$SANITIZED_ENV_FILE" \
  --volume "$DATA_DIR:/app/data" \
  --publish "127.0.0.1:$PORT:8080" \
  "$IMAGE_NAME:$IMAGE_TAG"

docker ps --filter "name=$CONTAINER_NAME"
