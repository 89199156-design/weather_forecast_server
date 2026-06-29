#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
ENV_FILE="${WEATHER_OPENMETEO_ENV_FILE:-$APP_DIR/config/singapore.example.env}"

declare -A WEATHER_ENV_OVERRIDES=()

capture_weather_env_overrides() {
  local name
  while IFS='=' read -r name _; do
    if [[ "$name" == WEATHER_* || "$name" == "REMOTE_DATA_DIRECTORY" || "$name" == "REMOTE_DATA_DIRECTORY_MINIMUM_AGE" || "$name" == "CACHE_FILE" || "$name" == "CACHE_SIZE" || "$name" == "BLOCK_SIZE" || "$name" == "CACHE_META_FILE" || "$name" == "CACHE_META_SIZE" ]]; then
      WEATHER_ENV_OVERRIDES["$name"]="${!name}"
    fi
  done < <(env)
}

restore_weather_env_overrides() {
  local name
  for name in "${!WEATHER_ENV_OVERRIDES[@]}"; do
    printf -v "$name" '%s' "${WEATHER_ENV_OVERRIDES[$name]}"
    export "$name"
  done
}

is_truthy() {
  local value="${1:-}"
  value="${value,,}"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" || "$value" == "on" ]]
}

floor_float() {
  awk -v value="$1" 'BEGIN {
    parsed = value + 0
    integer = int(parsed)
    if (parsed < 0 && parsed != integer) {
      integer -= 1
    }
    printf "%d\n", integer
  }'
}

dem_region_lat_bounds() {
  local lat_start
  local lat_end
  lat_start="$(floor_float "${WEATHER_REGION_BOTTOM_LAT:-0}")"
  lat_end="$(floor_float "${WEATHER_REGION_TOP_LAT:-58}")"
  if [[ "$lat_start" -lt -90 ]]; then
    lat_start=-90
  fi
  if [[ "$lat_end" -gt 89 ]]; then
    lat_end=89
  fi
  if [[ "$lat_start" -gt "$lat_end" ]]; then
    printf '%s\n' "Configured DEM region latitude range is empty." >&2
    exit 2
  fi
  printf '%s %s\n' "$lat_start" "$lat_end"
}

has_local_dem_static_files() {
  local lat_start
  local lat_end
  local lat
  read -r lat_start lat_end < <(dem_region_lat_bounds)
  for lat in $(seq "$lat_start" "$lat_end"); do
    if [[ ! -s "$DATA_DIR/copernicus_dem90/static/lat_${lat}.om" ]]; then
      return 1
    fi
  done
}

require_dem_source() {
  if ! is_truthy "$REQUIRE_DEM_SOURCE"; then
    return
  fi
  if [[ -n "${REMOTE_DATA_DIRECTORY:-}" ]]; then
    return
  fi
  if has_local_dem_static_files; then
    return
  fi

  printf '%s\n' \
    "Missing Copernicus DEM90 source. Set REMOTE_DATA_DIRECTORY to the Open-Meteo data URL, or pre-seed $DATA_DIR/copernicus_dem90/static/lat_*.om." >&2
  exit 2
}

capture_weather_env_overrides
if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi
restore_weather_env_overrides

default_image_tag() {
  git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || printf '%s' latest
}

IMAGE_NAME="${WEATHER_OPENMETEO_IMAGE:-weather-forecast-openmeteo}"
IMAGE_TAG="${WEATHER_OPENMETEO_TAG:-$(default_image_tag)}"
CONTAINER_NAME="${WEATHER_OPENMETEO_CONTAINER:-weather-forecast-openmeteo-candidate}"
PORT="${WEATHER_OPENMETEO_PORT:-18080}"
DATA_DIR="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}"
OPENMETEO_UID="${WEATHER_OPENMETEO_UID:-999}"
OPENMETEO_GID="${WEATHER_OPENMETEO_GID:-999}"
REQUIRE_DEM_SOURCE="${WEATHER_REQUIRE_DEM_SOURCE:-true}"

cd "$APP_DIR"
mkdir -p "$DATA_DIR"
chown "$OPENMETEO_UID:$OPENMETEO_GID" "$DATA_DIR"
require_dem_source

SANITIZED_ENV_FILE="$(mktemp)"
cleanup_sanitized_env() {
  rm -f "$SANITIZED_ENV_FILE"
}
trap cleanup_sanitized_env EXIT

env | sort | awk -F= '
  ($1 ~ /^WEATHER_/ || $1 == "REMOTE_DATA_DIRECTORY" || $1 == "REMOTE_DATA_DIRECTORY_MINIMUM_AGE" || $1 == "CACHE_FILE" || $1 == "CACHE_SIZE" || $1 == "BLOCK_SIZE" || $1 == "CACHE_META_FILE" || $1 == "CACHE_META_SIZE") && $2 != "" { print }
' > "$SANITIZED_ENV_FILE"

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --env-file "$SANITIZED_ENV_FILE" \
  --volume "$DATA_DIR:/app/data" \
  --publish "127.0.0.1:$PORT:8080" \
  "$IMAGE_NAME:$IMAGE_TAG"

docker ps --filter "name=$CONTAINER_NAME"
