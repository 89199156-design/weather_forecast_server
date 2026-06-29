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

capture_weather_env_overrides
if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi
restore_weather_env_overrides

read_cdsapi_key() {
  local candidate
  for candidate in \
    "${WEATHER_CAMS_CDSAPI_RC:-}" \
    "${CDSAPI_RC:-}" \
    "$HOME/.cdsapirc" \
    "/home/ubuntu/.cdsapirc" \
    "/root/.cdsapirc"; do
    if [[ -z "$candidate" || ! -r "$candidate" ]]; then
      continue
    fi
    awk '
      $0 ~ /^[[:space:]]*key[[:space:]]*:/ {
        sub(/^[[:space:]]*key[[:space:]]*:/, "", $0)
        sub(/^[[:space:]]+/, "", $0)
        sub(/[[:space:]]+$/, "", $0)
        print $0
        exit
      }
    ' "$candidate"
    return
  done
}

if [[ -z "${WEATHER_CAMS_ADS_KEY:-}" && -z "${WEATHER_CAMS_CDS_KEY:-}" ]]; then
  WEATHER_CAMS_ADS_KEY="$(read_cdsapi_key)"
  export WEATHER_CAMS_ADS_KEY
fi

default_image_tag() {
  git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || printf '%s' latest
}

IMAGE_NAME="${WEATHER_OPENMETEO_IMAGE:-weather-forecast-openmeteo}"
IMAGE_TAG="${WEATHER_OPENMETEO_TAG:-$(default_image_tag)}"
DATA_DIR="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}"
OPENMETEO_UID="${WEATHER_OPENMETEO_UID:-999}"
OPENMETEO_GID="${WEATHER_OPENMETEO_GID:-999}"
GFS_MAX_FORECAST_HOUR="${WEATHER_GFS_MAX_FORECAST_HOUR:-120}"
GFS_CONCURRENT="${WEATHER_GFS_DOWNLOAD_CONCURRENT:-4}"
CAMS_CONCURRENT="${WEATHER_CAMS_DOWNLOAD_CONCURRENT:-1}"
GFS_DOWNLOAD_MODE="${WEATHER_GFS_DOWNLOAD_MODE:-raw}"
GFS_FILTER_DOWNLOAD="${WEATHER_GFS_FILTER_DOWNLOAD:-false}"
ALLOW_GLOBAL_RAW_DOWNLOAD="${WEATHER_ALLOW_GLOBAL_RAW_DOWNLOAD:-false}"
CAMS_AREA_DOWNLOAD="${WEATHER_CAMS_AREA_DOWNLOAD:-false}"
CAMS_ADS_KEY="${WEATHER_CAMS_ADS_KEY:-${WEATHER_CAMS_CDS_KEY:-}}"
OPENMETEO_SYNC_BASE_URL="${WEATHER_OPENMETEO_SYNC_BASE_URL:-}"
OPENMETEO_SYNC_PAST_DAYS="${WEATHER_OPENMETEO_SYNC_PAST_DAYS:-2}"
OPENMETEO_SYNC_CONCURRENT="${WEATHER_OPENMETEO_SYNC_CONCURRENT:-4}"
GFS_UPPER_LEVELS="${WEATHER_GFS_UPPER_LEVELS:-10,15,20,30,40,50,70,100,125,150,175,200,225,250,275,300,325,350,375,400,425,450,475,500,525,550,575,600,625,650,675,700,725,750,775,800,825,850,875,900,925,950,975,1000}"
GFS_UPPER_LEVEL_PGRB2_LEVELS="${WEATHER_GFS_UPPER_LEVEL_PGRB2_LEVELS:-10,15,20,30,40,50,70,100,150,200,250,300,350,400,450,500,550,600,650,700,750,800,850,900,925,950,975,1000}"
GFS_UPPER_LEVEL_VARIABLES="${WEATHER_GFS_UPPER_LEVEL_VARIABLES:-temperature,wind_u_component,wind_v_component,geopotential_height,cloud_cover,relative_humidity,vertical_velocity}"
GFS_UPPER_LEVEL_CHUNK_SIZE="${WEATHER_GFS_UPPER_LEVEL_CHUNK_SIZE:-4}"
GFS_UPPER_LEVEL_CONCURRENT="${WEATHER_GFS_UPPER_LEVEL_DOWNLOAD_CONCURRENT:-1}"
GFS013_SYNC_VARIABLES="${WEATHER_GFS013_SYNC_VARIABLES:-temperature_2m,temperature_80m,temperature_100m,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,pressure_msl,relative_humidity_2m,precipitation,wind_v_component_10m,wind_u_component_10m,wind_v_component_80m,wind_u_component_80m,wind_v_component_100m,wind_u_component_100m,surface_temperature,soil_temperature_0_to_10cm,soil_temperature_10_to_40cm,soil_temperature_40_to_100cm,soil_temperature_100_to_200cm,soil_moisture_0_to_10cm,soil_moisture_10_to_40cm,soil_moisture_40_to_100cm,soil_moisture_100_to_200cm,snow_depth,sensible_heat_flux,latent_heat_flux,showers,snowfall_water_equivalent,shortwave_radiation,diffuse_radiation,uv_index,uv_index_clear_sky,boundary_layer_height,total_column_integrated_water_vapour}"
GFS025_SURFACE_SYNC_VARIABLES="${WEATHER_GFS025_SURFACE_SYNC_VARIABLES:-temperature_80m,temperature_100m,wind_u_component_80m,wind_v_component_80m,wind_u_component_100m,wind_v_component_100m,wind_gusts_10m,visibility,cape,lifted_index,convective_inhibition,freezing_level_height,categorical_freezing_rain}"
GFS013_RUN="${WEATHER_GFS013_RUN:-}"
GFS025_RUN="${WEATHER_GFS025_RUN:-}"
SKIP_GFS013_DOWNLOAD="${WEATHER_SKIP_GFS013_DOWNLOAD:-false}"
SKIP_GFS025_SURFACE_DOWNLOAD="${WEATHER_SKIP_GFS025_SURFACE_DOWNLOAD:-false}"
SKIP_GFS025_UPPER_LEVEL_DOWNLOAD="${WEATHER_SKIP_GFS025_UPPER_LEVEL_DOWNLOAD:-false}"
SKIP_CAMS_DOWNLOAD="${WEATHER_SKIP_CAMS_DOWNLOAD:-false}"
REQUIRE_DEM_SOURCE="${WEATHER_REQUIRE_DEM_SOURCE:-true}"
DEM_PRESEED_ENABLED="${WEATHER_DEM_PRESEED_ENABLED:-false}"
DEM_PRESEED_BASE_URL="${WEATHER_DEM_PRESEED_BASE_URL:-https://openmeteo.s3.amazonaws.com/data/copernicus_dem90/static}"
DEM_PRESEED_CONCURRENT="${WEATHER_DEM_PRESEED_CONCURRENT:-4}"

cd "$APP_DIR"
mkdir -p "$DATA_DIR"

SANITIZED_ENV_FILE="$(mktemp)"

cleanup_sanitized_env() {
  rm -f "$SANITIZED_ENV_FILE"
}
trap cleanup_sanitized_env EXIT

env | sort | awk -F= '
  ($1 ~ /^WEATHER_/ || $1 == "REMOTE_DATA_DIRECTORY" || $1 == "REMOTE_DATA_DIRECTORY_MINIMUM_AGE" || $1 == "CACHE_FILE" || $1 == "CACHE_SIZE" || $1 == "BLOCK_SIZE" || $1 == "CACHE_META_FILE" || $1 == "CACHE_META_SIZE") && $2 != "" { print }
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

append_sync_server_arg() {
  if [[ -n "$OPENMETEO_SYNC_BASE_URL" ]]; then
    printf '%s\n' "--server"
    printf '%s\n' "$OPENMETEO_SYNC_BASE_URL"
  fi
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

gfs025_pressure_sync_variables() {
  local IFS=","
  local variable
  local level
  local variables=()
  local levels=()
  local items=()

  read -ra variables <<< "$GFS_UPPER_LEVEL_VARIABLES"
  read -ra levels <<< "$GFS_UPPER_LEVELS"
  for variable in "${variables[@]}"; do
    variable="${variable//[[:space:]]/}"
    if [[ -z "$variable" ]]; then
      continue
    fi
    for level in "${levels[@]}"; do
      level="${level//[[:space:]]/}"
      if [[ -z "$level" ]]; then
        continue
      fi
      if [[ "$variable" == "cloud_cover" && ( "$level" -lt 50 || "$level" == "70" ) ]]; then
        continue
      fi
      items+=("${variable}_${level}hPa")
    done
  done
  (IFS=","; printf '%s\n' "${items[*]}")
}

sync_openmeteo_database() {
  local models="$1"
  local variables="$2"

  run_openmeteo sync "$models" "$variables" \
    $(append_sync_server_arg) \
    --past-days "$OPENMETEO_SYNC_PAST_DAYS" \
    --concurrent "$OPENMETEO_SYNC_CONCURRENT"
}

# gfs_global in Open-Meteo mixes gfs013 with gfs025. The normal Singapore
# production path is WEATHER_GFS_DOWNLOAD_MODE=raw, which lets the unmodified
# Open-Meteo downloader convert original source files into local `.om` chunks.
# Keep sync mode as a manual reference/debug path for explicitly approved
# processed `.om` mirrors.
require_dem_source

case "$GFS_DOWNLOAD_MODE" in
  sync)
    if is_truthy "$SKIP_GFS013_DOWNLOAD"; then
      printf '%s\n' "Skipping GFS013 sync: WEATHER_SKIP_GFS013_DOWNLOAD is enabled."
    else
      sync_openmeteo_database ncep_gfs013 "$GFS013_SYNC_VARIABLES"
    fi

    if is_truthy "$SKIP_GFS025_SURFACE_DOWNLOAD"; then
      printf '%s\n' "Skipping GFS025 surface sync: WEATHER_SKIP_GFS025_SURFACE_DOWNLOAD is enabled."
    else
      sync_openmeteo_database ncep_gfs025 "$GFS025_SURFACE_SYNC_VARIABLES"
    fi

    if is_truthy "$SKIP_GFS025_UPPER_LEVEL_DOWNLOAD"; then
      printf '%s\n' "Skipping GFS025 pressure-level sync: WEATHER_SKIP_GFS025_UPPER_LEVEL_DOWNLOAD is enabled."
    else
      sync_openmeteo_database ncep_gfs025 "$(gfs025_pressure_sync_variables)"
    fi
    ;;
  raw)
    if ! is_truthy "$GFS_FILTER_DOWNLOAD" && ! is_truthy "$ALLOW_GLOBAL_RAW_DOWNLOAD"; then
      printf '%s\n' \
        "Refusing global Open-Meteo raw GFS download. Enable WEATHER_GFS_FILTER_DOWNLOAD=true for China-region GFS, or set WEATHER_ALLOW_GLOBAL_RAW_DOWNLOAD=true only for an explicitly approved diagnostic run." >&2
      exit 2
    fi

    if is_truthy "$SKIP_GFS013_DOWNLOAD"; then
      printf '%s\n' "Skipping GFS013 download: WEATHER_SKIP_GFS013_DOWNLOAD is enabled."
    else
      run_openmeteo download-gfs gfs013 \
        $(append_run_arg "$GFS013_RUN") \
        --max-forecast-hour "$GFS_MAX_FORECAST_HOUR" \
        --concurrent "$GFS_CONCURRENT"
    fi

    if is_truthy "$SKIP_GFS025_SURFACE_DOWNLOAD"; then
      printf '%s\n' "Skipping GFS025 surface download: WEATHER_SKIP_GFS025_SURFACE_DOWNLOAD is enabled."
    else
      run_openmeteo download-gfs gfs025 \
        $(append_run_arg "$GFS025_RUN") \
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
    ;;
  *)
    printf '%s\n' "WEATHER_GFS_DOWNLOAD_MODE must be 'sync' or 'raw'." >&2
    exit 2
    ;;
esac

if is_truthy "$SKIP_CAMS_DOWNLOAD"; then
  printf '%s\n' "Skipping CAMS global download: WEATHER_SKIP_CAMS_DOWNLOAD is enabled."
elif is_truthy "$CAMS_AREA_DOWNLOAD" && [[ -n "$CAMS_ADS_KEY" ]]; then
  run_openmeteo download-cams cams_global \
    --concurrent "$CAMS_CONCURRENT"
elif is_truthy "$CAMS_AREA_DOWNLOAD"; then
  printf '%s\n' "Skipping CAMS global area download: WEATHER_CAMS_ADS_KEY/WEATHER_CAMS_CDS_KEY is not set."
elif [[ -n "${WEATHER_CAMS_FTP_USER:-}" && -n "${WEATHER_CAMS_FTP_PASSWORD:-}" ]]; then
  run_openmeteo download-cams cams_global \
    --ftpuser "$WEATHER_CAMS_FTP_USER" \
    --ftppassword "$WEATHER_CAMS_FTP_PASSWORD" \
    --concurrent "$CAMS_CONCURRENT"
else
  printf '%s\n' "Skipping CAMS global download: WEATHER_CAMS_FTP_USER/WEATHER_CAMS_FTP_PASSWORD are not set."
fi
