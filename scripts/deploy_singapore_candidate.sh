#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
IMAGE_NAME="${WEATHER_OPENMETEO_IMAGE:-weather-forecast-openmeteo}"
IMAGE_TAG="${WEATHER_OPENMETEO_TAG:-latest}"
CONTAINER_NAME="${WEATHER_OPENMETEO_CONTAINER:-weather-forecast-openmeteo-candidate}"
PORT="${WEATHER_OPENMETEO_PORT:-18080}"
DATA_DIR="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}"
ENV_FILE="${WEATHER_OPENMETEO_ENV_FILE:-$APP_DIR/config/singapore.example.env}"
OPENMETEO_UID="${WEATHER_OPENMETEO_UID:-999}"
OPENMETEO_GID="${WEATHER_OPENMETEO_GID:-999}"

cd "$APP_DIR"
mkdir -p "$DATA_DIR"
chown "$OPENMETEO_UID:$OPENMETEO_GID" "$DATA_DIR"

SANITIZED_ENV_FILE="$(mktemp)"
cleanup_sanitized_env() {
  rm -f "$SANITIZED_ENV_FILE"
}
trap cleanup_sanitized_env EXIT

awk '
  /^[[:space:]]*($|#)/ { print; next }
  $0 !~ /^[[:space:]]*[^#][^=]*=[[:space:]]*$/ { print; next }
' "$ENV_FILE" > "$SANITIZED_ENV_FILE"

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --env-file "$SANITIZED_ENV_FILE" \
  --volume "$DATA_DIR:/app/data" \
  --publish "127.0.0.1:$PORT:8080" \
  "$IMAGE_NAME:$IMAGE_TAG"

docker ps --filter "name=$CONTAINER_NAME"
