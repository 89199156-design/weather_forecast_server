#!/usr/bin/env bash
# Shared shell helpers for Open-Meteo runtime production scripts.

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
  source <(sed 's/\r$//' "$file")
}

load_weather_env() {
  capture_weather_env_overrides
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    source_env_file "$ENV_FILE"
    set +a
  fi
  restore_weather_env_overrides
}

is_truthy() {
  local value="${1:-}"
  value="${value,,}"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" || "$value" == "on" ]]
}

resolve_openmeteo_cpu_limit() {
  local requested="${1:-1.5}"
  local online_cpus

  online_cpus="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || printf '1\n')"
  awk -v requested="$requested" -v online="$online_cpus" 'BEGIN {
    if (requested !~ /^[0-9]+([.][0-9]+)?$/ || requested + 0 <= 0) {
      print "WEATHER_OPENMETEO_CPU_LIMIT must be a positive number" > "/dev/stderr"
      exit 2
    }
    if (online !~ /^[0-9]+$/ || online + 0 < 1) {
      online = 1
    }
    safe = online - 0.5
    if (safe < 0.5) {
      safe = 0.5
    }
    effective = requested + 0
    if (effective > safe) {
      effective = safe
    }
    printf "%g\n", effective
  }'
}

prepare_openmeteo_staging_permissions() {
  local staging_dir="${1:?staging directory is required}"
  local producer_root="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"
  local owner_uid="${WEATHER_OPENMETEO_UID:-999}"
  local owner_gid="${WEATHER_OPENMETEO_GID:-999}"

  case "$staging_dir" in
    "$producer_root/staging/"*) ;;
    *)
      printf '%s\n' "Refusing to change directory ownership outside producer staging: $staging_dir" >&2
      return 2
      ;;
  esac
  if [[ "$(id -u)" -ne 0 ]]; then
    printf '%s\n' "Producer staging permissions must be prepared as root: $staging_dir" >&2
    return 2
  fi

  # copytree(..., copy_function=os.link) creates fresh directories owned by
  # the producer process while the linked OM files retain the container UID.
  # Change directories only, so the container can create its atomic temporary
  # files without chowning or modifying the immutable current coverage.
  find "$staging_dir" -type d -exec chown "$owner_uid:$owner_gid" {} +
}

openmeteo_set_runtime_defaults() {
  IMAGE_NAME="${WEATHER_OPENMETEO_IMAGE:-weather-forecast-openmeteo}"
  IMAGE_TAG="${WEATHER_OPENMETEO_TAG:-}"
  if [[ -z "$IMAGE_TAG" ]]; then
    printf '%s\n' "WEATHER_OPENMETEO_TAG must be the exact immutable native-* image tag." >&2
    return 2
  fi
  DATA_DIR="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/point}"
  OPENMETEO_UID="${WEATHER_OPENMETEO_UID:-999}"
  OPENMETEO_GID="${WEATHER_OPENMETEO_GID:-999}"
  OPENMETEO_HTTP_CACHE_ENABLED="${WEATHER_OPENMETEO_HTTP_CACHE_ENABLED:-true}"
  OPENMETEO_HTTP_CACHE_DIR="${WEATHER_OPENMETEO_HTTP_CACHE_DIR:-/app/data/http_cache}"
  OPENMETEO_HTTP_CACHE_CLEANUP="${WEATHER_OPENMETEO_HTTP_CACHE_CLEANUP:-true}"
  REQUIRE_DEM_SOURCE="${WEATHER_REQUIRE_DEM_SOURCE:-true}"
  DEM_PRESEED_ENABLED="${WEATHER_DEM_PRESEED_ENABLED:-false}"
  DEM_PRESEED_BASE_URL="${WEATHER_DEM_PRESEED_BASE_URL:-}"
  DEM_PRESEED_CONCURRENT="${WEATHER_DEM_PRESEED_CONCURRENT:-4}"
  OPENMETEO_CPU_LIMIT="$(resolve_openmeteo_cpu_limit "${WEATHER_OPENMETEO_CPU_LIMIT:-1.5}")"
  OPENMETEO_CPU_SHARES="${WEATHER_OPENMETEO_CPU_SHARES:-256}"
  OPENMETEO_BLKIO_WEIGHT="${WEATHER_OPENMETEO_BLKIO_WEIGHT:-100}"

  cd "$APP_DIR"
  mkdir -p "$DATA_DIR"

  local openmeteo_http_cache_enabled="${OPENMETEO_HTTP_CACHE_ENABLED,,}"
  if [[ "$openmeteo_http_cache_enabled" == "1" || "$openmeteo_http_cache_enabled" == "true" || "$openmeteo_http_cache_enabled" == "yes" || "$openmeteo_http_cache_enabled" == "on" ]]; then
    HTTP_CACHE="${HTTP_CACHE:-$OPENMETEO_HTTP_CACHE_DIR}"
    export HTTP_CACHE
  fi

  if [[ "$(id -u)" -eq 0 ]]; then
    if is_truthy "${WEATHER_OPENMETEO_CHOWN_RECURSIVE:-false}"; then
      chown -R "$OPENMETEO_UID:$OPENMETEO_GID" "$DATA_DIR"
    else
      chown "$OPENMETEO_UID:$OPENMETEO_GID" "$DATA_DIR"
    fi
  fi
}

write_sanitized_env_file() {
  SANITIZED_ENV_FILE="$(mktemp)"
  env | sort | awk -F= '
    ($1 ~ /^WEATHER_/ || $1 == "HTTP_CACHE" || $1 == "DATA_RUN_DIRECTORY" || $1 == "CACHE_FILE" || $1 == "CACHE_SIZE" || $1 == "BLOCK_SIZE" || $1 == "CACHE_META_FILE" || $1 == "CACHE_META_SIZE") && $2 != "" { print }
  ' > "$SANITIZED_ENV_FILE"
}

run_openmeteo() {
  docker run --rm \
    --cpus "$OPENMETEO_CPU_LIMIT" \
    --cpu-shares "$OPENMETEO_CPU_SHARES" \
    --blkio-weight "$OPENMETEO_BLKIO_WEIGHT" \
    --env-file "$SANITIZED_ENV_FILE" \
    --volume "$DATA_DIR:/app/data" \
    "$IMAGE_NAME:$IMAGE_TAG" \
    "$@"
}

append_run_arg() {
  local run_value="${1:-}"
  if [[ -n "$run_value" ]]; then
    printf '%s\n' "--run"
    printf '%s\n' "$run_value"
  fi
}

cleanup_download_work_dirs() {
  local path
  for path in "$@"; do
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
  if [[ -z "$DEM_PRESEED_BASE_URL" ]]; then
    printf '%s\n' "WEATHER_DEM_PRESEED_BASE_URL is required when WEATHER_DEM_PRESEED_ENABLED is true." >&2
    exit 2
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

cleanup_openmeteo_http_cache() {
  if is_truthy "$OPENMETEO_HTTP_CACHE_CLEANUP"; then
    local cache_dir_host
    cache_dir_host="$(host_http_cache_dir)"
    if [[ -n "${cache_dir_host:-}" && "$cache_dir_host" == "$DATA_DIR"/* ]]; then
      if [[ -d "$cache_dir_host" ]]; then
        local cache_entries=()
        shopt -s dotglob nullglob
        cache_entries=("$cache_dir_host"/*)
        shopt -u dotglob nullglob
        if [[ "${#cache_entries[@]}" -gt 0 ]]; then
          rm -rf -- "${cache_entries[@]}"
        fi
      fi
    fi
  fi
}

prepare_openmeteo_http_cache() {
  local cache_dir_host
  cache_dir_host="$(host_http_cache_dir)"
  if [[ -z "${cache_dir_host:-}" ]]; then
    return
  fi
  if [[ "$cache_dir_host" != "$DATA_DIR"/* ]]; then
    printf '%s\n' "Refusing to prepare HTTP cache outside DATA_DIR: $cache_dir_host" >&2
    exit 2
  fi
  mkdir -p "$cache_dir_host"
  if [[ "$(id -u)" -eq 0 ]]; then
    chown -R "$OPENMETEO_UID:$OPENMETEO_GID" "$cache_dir_host"
  else
    # The cache contains only disposable public source downloads. The host
    # scheduler and the fixed container UID both need to recreate/clear it.
    chmod 0777 "$cache_dir_host"
  fi
}
