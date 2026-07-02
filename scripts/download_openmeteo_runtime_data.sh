#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
DEFAULT_ENV_FILE="$APP_DIR/config/singapore.private.env"
if [[ ! -f "$DEFAULT_ENV_FILE" ]]; then
  DEFAULT_ENV_FILE="$APP_DIR/config/singapore.example.env"
fi
ENV_FILE="${WEATHER_OPENMETEO_ENV_FILE:-$DEFAULT_ENV_FILE}"

declare -A WEATHER_ENV_OVERRIDES=()

capture_weather_env_overrides() {
  local name
  while IFS='=' read -r name _; do
    if [[ "$name" == WEATHER_* || "$name" == "HTTP_CACHE" || "$name" == "DATA_RUN_DIRECTORY" || "$name" == "CACHE_FILE" || "$name" == "CACHE_SIZE" || "$name" == "BLOCK_SIZE" || "$name" == "CACHE_META_FILE" || "$name" == "CACHE_META_SIZE" ]]; then
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

source_env_file() {
  local file="$1"
  # Normalize CRLF so Windows-side packaging cannot break bash source.
  source <(sed 's/\r$//' "$file")
}

capture_weather_env_overrides
if [[ -f "$ENV_FILE" ]]; then
  set -a
  source_env_file "$ENV_FILE"
  set +a
fi
restore_weather_env_overrides

default_image_tag() {
  git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || printf '%s' latest
}

IMAGE_NAME="${WEATHER_OPENMETEO_IMAGE:-weather-forecast-openmeteo}"
IMAGE_TAG="${WEATHER_OPENMETEO_TAG:-$(default_image_tag)}"
DATA_DIR="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}"
OPENMETEO_UID="${WEATHER_OPENMETEO_UID:-999}"
OPENMETEO_GID="${WEATHER_OPENMETEO_GID:-999}"
OPENMETEO_HTTP_CACHE_ENABLED="${WEATHER_OPENMETEO_HTTP_CACHE_ENABLED:-true}"
OPENMETEO_HTTP_CACHE_DIR="${WEATHER_OPENMETEO_HTTP_CACHE_DIR:-/app/data/http_cache}"
OPENMETEO_HTTP_CACHE_CLEANUP="${WEATHER_OPENMETEO_HTTP_CACHE_CLEANUP:-true}"
GFS_MAX_FORECAST_HOUR="${WEATHER_GFS_MAX_FORECAST_HOUR:-120}"
GFS_CONCURRENT="${WEATHER_GFS_DOWNLOAD_CONCURRENT:-4}"
CAMS_CONCURRENT="${WEATHER_CAMS_DOWNLOAD_CONCURRENT:-1}"
CAMS_FTP_USER="${WEATHER_CAMS_FTP_USER:-}"
CAMS_FTP_PASSWORD="${WEATHER_CAMS_FTP_PASSWORD:-}"
CAMS_VARIABLES="${WEATHER_CAMS_VARIABLES:-pm2_5,pm10,aerosol_optical_depth,dust,carbon_monoxide,nitrogen_dioxide,ozone,sulphur_dioxide}"
GFS_UPPER_LEVELS="${WEATHER_GFS_UPPER_LEVELS:-1000,975,950,925,900,850,800,750,700,650,600,550,500,400,300,200}"
GFS_UPPER_LEVEL_PGRB2_LEVELS="${WEATHER_GFS_UPPER_LEVEL_PGRB2_LEVELS:-1000,975,950,925,900,850,800,750,700,650,600,550,500,400,300,200}"
GFS_UPPER_LEVEL_VARIABLES="${WEATHER_GFS_UPPER_LEVEL_VARIABLES:-temperature,wind_u_component,wind_v_component,geopotential_height,cloud_cover,relative_humidity,vertical_velocity}"
GFS_UPPER_LEVEL_CHUNK_SIZE="${WEATHER_GFS_UPPER_LEVEL_CHUNK_SIZE:-4}"
GFS_UPPER_LEVEL_CONCURRENT="${WEATHER_GFS_UPPER_LEVEL_DOWNLOAD_CONCURRENT:-1}"
GFS013_RUN="${WEATHER_GFS013_RUN:-}"
GFS025_RUN="${WEATHER_GFS025_RUN:-}"
CAMS_RUN="${WEATHER_CAMS_RUN:-}"
REQUIRE_DEM_SOURCE="${WEATHER_REQUIRE_DEM_SOURCE:-true}"
DEM_PRESEED_ENABLED="${WEATHER_DEM_PRESEED_ENABLED:-false}"
DEM_PRESEED_BASE_URL="${WEATHER_DEM_PRESEED_BASE_URL:-}"
DEM_PRESEED_CONCURRENT="${WEATHER_DEM_PRESEED_CONCURRENT:-4}"

cd "$APP_DIR"
mkdir -p "$DATA_DIR"

openmeteo_http_cache_enabled="${OPENMETEO_HTTP_CACHE_ENABLED,,}"
if [[ "$openmeteo_http_cache_enabled" == "1" || "$openmeteo_http_cache_enabled" == "true" || "$openmeteo_http_cache_enabled" == "yes" || "$openmeteo_http_cache_enabled" == "on" ]]; then
  HTTP_CACHE="${HTTP_CACHE:-$OPENMETEO_HTTP_CACHE_DIR}"
  export HTTP_CACHE
fi

SANITIZED_ENV_FILE="$(mktemp)"

cleanup_sanitized_env() {
  rm -f "$SANITIZED_ENV_FILE"
}
trap cleanup_sanitized_env EXIT

env | sort | awk -F= '
  ($1 ~ /^WEATHER_/ || $1 == "HTTP_CACHE" || $1 == "DATA_RUN_DIRECTORY" || $1 == "CACHE_FILE" || $1 == "CACHE_SIZE" || $1 == "BLOCK_SIZE" || $1 == "CACHE_META_FILE" || $1 == "CACHE_META_SIZE") && $2 != "" { print }
' > "$SANITIZED_ENV_FILE"

is_truthy() {
  local value="${1:-}"
  value="${value,,}"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" || "$value" == "on" ]]
}

if [[ "$(id -u)" -eq 0 ]]; then
  if is_truthy "${WEATHER_OPENMETEO_CHOWN_RECURSIVE:-false}"; then
    chown -R "$OPENMETEO_UID:$OPENMETEO_GID" "$DATA_DIR"
  else
    chown "$OPENMETEO_UID:$OPENMETEO_GID" "$DATA_DIR"
  fi
fi

append_run_arg() {
  local run_value="${1:-}"
  if [[ -n "$run_value" ]]; then
    printf '%s\n' "--run"
    printf '%s\n' "$run_value"
  fi
}

run_openmeteo() {
  docker run --rm \
    --env-file "$SANITIZED_ENV_FILE" \
    --volume "$DATA_DIR:/app/data" \
    "$IMAGE_NAME:$IMAGE_TAG" \
    "$@"
}

cleanup_download_work_dirs() {
  local path
  local targets=(
    "$DATA_DIR/download-ncep_gfs013"
    "$DATA_DIR/download-ncep_gfs025"
    "$DATA_DIR/download-cams_global"
  )

  for path in "${targets[@]}"; do
    case "$path" in
      "$DATA_DIR"/*)
        rm -rf -- "$path"
        ;;
      *)
        printf '%s\n' "Refusing to remove path outside DATA_DIR: $path" >&2
        exit 2
        ;;
    esac
  done
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

preseed_dem_region_static_files() {
  if ! is_truthy "$DEM_PRESEED_ENABLED"; then
    return
  fi
  if has_local_dem_static_files; then
    return
  fi

  if ! [[ "$DEM_PRESEED_CONCURRENT" =~ ^[0-9]+$ ]] || [[ "$DEM_PRESEED_CONCURRENT" -lt 1 ]]; then
    printf '%s\n' "WEATHER_DEM_PRESEED_CONCURRENT must be a positive integer." >&2
    exit 2
  fi

  local lat_start
  local lat_end
  read -r lat_start lat_end < <(dem_region_lat_bounds)

  local dem_dir="$DATA_DIR/copernicus_dem90/static"
  mkdir -p "$dem_dir"

  local active=0
  local lat
  for lat in $(seq "$lat_start" "$lat_end"); do
    (
      local target="$dem_dir/lat_${lat}.om"
      local tmp="$target.tmp.$$"
      if [[ -s "$target" ]]; then
        exit 0
      fi
      printf '%s\n' "Downloading DEM90 static latitude $lat..."
      curl -fL --retry 5 --retry-delay 2 --retry-all-errors \
        -o "$tmp" \
        "${DEM_PRESEED_BASE_URL%/}/lat_${lat}.om"
      mv "$tmp" "$target"
    ) &
    active=$((active + 1))
    if [[ "$active" -ge "$DEM_PRESEED_CONCURRENT" ]]; then
      wait -n
      active=$((active - 1))
    fi
  done
  wait
}

require_dem_source() {
  preseed_dem_region_static_files

  if ! is_truthy "$REQUIRE_DEM_SOURCE"; then
    return
  fi
  if has_local_dem_static_files; then
    return
  fi

  printf '%s\n' \
    "Missing Copernicus DEM90 source. Set WEATHER_DEM_PRESEED_BASE_URL to a project-owned DEM mirror, or pre-seed $DATA_DIR/copernicus_dem90/static/lat_*.om." >&2
  exit 2
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
      $(append_run_arg "$GFS025_RUN") \
      --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
      --concurrent "$GFS_UPPER_LEVEL_CONCURRENT"
  done < <(upper_level_only_variable_chunks "$variable")
}

require_dem_source
cleanup_download_work_dirs

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

if [[ -z "$CAMS_FTP_USER" || -z "$CAMS_FTP_PASSWORD" ]]; then
  printf '%s\n' "Both WEATHER_CAMS_FTP_USER and WEATHER_CAMS_FTP_PASSWORD are required for CAMS FTP/ECPDS download." >&2
  exit 2
fi

cleanup_download_work_dirs "$DATA_DIR/download-cams_global"
run_openmeteo download-cams cams_global \
  $(append_run_arg "$CAMS_RUN") \
  --only-variables "$CAMS_VARIABLES" \
  --concurrent "$CAMS_CONCURRENT"

host_http_cache_dir() {
  if [[ -z "${HTTP_CACHE:-}" ]]; then
    return
  fi
  if [[ "$HTTP_CACHE" == /app/data/* ]]; then
    printf '%s\n' "$DATA_DIR/${HTTP_CACHE#/app/data/}"
    return
  fi
  if [[ "$HTTP_CACHE" == "$DATA_DIR"/* ]]; then
    printf '%s\n' "$HTTP_CACHE"
  fi
}

if is_truthy "$OPENMETEO_HTTP_CACHE_CLEANUP"; then
  CACHE_DIR_HOST="$(host_http_cache_dir)"
  if [[ -n "${CACHE_DIR_HOST:-}" && "$CACHE_DIR_HOST" == "$DATA_DIR"/* ]]; then
    rm -rf "$CACHE_DIR_HOST"
  fi
fi
