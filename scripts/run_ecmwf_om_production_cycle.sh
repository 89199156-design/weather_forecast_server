#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
EXPECTED_TASK="weather_ecmwf_probe_cycle"
if [[ "${WEATHER_1PANEL_VERIFIED_TASK:-}" != "$EXPECTED_TASK" ]]; then
  printf '%s\n' "拒绝执行：ECMWF OM 生产阶段必须来自已验证的 1Panel 流水线" >&2
  exit 2
fi
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env

RUN="${1:-${WEATHER_ECMWF_RUN:-}}"
if [[ ! "$RUN" =~ ^[0-9]{10}$ || "${RUN:8:2}" != "00" ]]; then
  printf '%s\n' "Usage: run_ecmwf_om_production_cycle.sh YYYYMMDD00" >&2
  exit 2
fi

ECMWF_ROOT="${WEATHER_ECMWF_ROOT:-$APP_DIR/data/ecmwf}"
STAGING_DIR="$ECMWF_ROOT/staging/ecmwf_$RUN"
PROGRESS_PATH="$STAGING_DIR/production-progress.json"
CURRENT_MARKER="$ECMWF_ROOT/groups/ecmwf/current/ready_for_processing.json"
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
IMAGE_NAME="${WEATHER_ECMWF_OPENMETEO_IMAGE:-weather-forecast-ecmwf}"
IMAGE_TAG="${WEATHER_ECMWF_OPENMETEO_TAG:-}"
LOOKBACK_HOURS="${WEATHER_ECMWF_ROLLING_LOOKBACK_HOURS:-72}"
MINIMUM_START_FREE_BYTES="${WEATHER_ECMWF_MINIMUM_START_FREE_BYTES:-12884901888}"
MINIMUM_RUNTIME_FREE_BYTES="${WEATHER_ECMWF_MINIMUM_RUNTIME_FREE_BYTES:-4294967296}"
SOURCE_REVISION="$(git -C "$APP_DIR" rev-parse HEAD)"
PATCH_PATH="$APP_DIR/vendor/patches/open-meteo-ecmwf-regional.patch"

[[ -n "$IMAGE_TAG" ]] || { printf '%s\n' "WEATHER_ECMWF_OPENMETEO_TAG is required" >&2; exit 2; }
[[ -f "$PATCH_PATH" ]] || { printf '%s\n' "Missing ECMWF regional source patch" >&2; exit 1; }
PATCH_SHA256="$(sha256sum "$PATCH_PATH" | awk '{print $1}')"
IMAGE_REF="$IMAGE_NAME:$IMAGE_TAG"

available_bytes() {
  df -PB1 "$ECMWF_ROOT" | awk 'NR == 2 {print $4}'
}

require_free_bytes() {
  local required="$1"
  local stage="$2"
  local available
  available="$(available_bytes)"
  if [[ ! "$available" =~ ^[0-9]+$ || "$available" -lt "$required" ]]; then
    printf '%s\n' \
      "ECMWF disk preflight failed stage=$stage available=$available required=$required" >&2
    exit 1
  fi
}

mkdir -p "$ECMWF_ROOT/staging" "$ECMWF_ROOT/releases" "$LOG_DIR"
require_free_bytes "$MINIMUM_START_FREE_BYTES" start

for peer in gfs cams cams-ads; do
  if docker ps --format '{{.Names}}' | grep -Fxq "weather-openmeteo-$peer"; then
    printf '%s\n' "ECMWF production refuses concurrent model container: $peer" >&2
    exit 1
  fi
done

export WEATHER_OPENMETEO_IMAGE="$IMAGE_NAME"
export WEATHER_OPENMETEO_TAG="$IMAGE_TAG"
export WEATHER_OPENMETEO_DATA_DIR="$STAGING_DIR"
export WEATHER_OPENMETEO_TASK_SCOPE=ecmwf
export WEATHER_OPENMETEO_HTTP_CACHE_ENABLED=false
export DATA_DIRECTORY=/app/data/
export CACHE_SIZE="${WEATHER_ECMWF_CACHE_SIZE:-2GB}"
export CACHE_META_SIZE="${WEATHER_ECMWF_CACHE_META_SIZE:-1MB}"
export WEATHER_ECMWF_REGIONAL_GRID=true
export WEATHER_ECMWF_STORAGE_LEFT_LON="${WEATHER_ECMWF_STORAGE_LEFT_LON:-68}"
export WEATHER_ECMWF_STORAGE_RIGHT_LON="${WEATHER_ECMWF_STORAGE_RIGHT_LON:-142}"
export WEATHER_ECMWF_STORAGE_BOTTOM_LAT="${WEATHER_ECMWF_STORAGE_BOTTOM_LAT:--2}"
export WEATHER_ECMWF_STORAGE_TOP_LAT="${WEATHER_ECMWF_STORAGE_TOP_LAT:-60}"
openmeteo_set_runtime_defaults
write_sanitized_env_file
trap 'rm -f "${SANITIZED_ENV_FILE:-}"' EXIT

RAW_VARIABLES="$(PYTHONPATH="$APP_DIR/scripts" python3 - <<'PY'
from ecmwf_contract import RAW_VARIABLES
print(",".join(RAW_VARIABLES))
PY
)"
FALLBACK_VARIABLES="$(PYTHONPATH="$APP_DIR/scripts" python3 - <<'PY'
from ecmwf_contract import ROLLING_FALLBACK_VARIABLES
print(",".join(ROLLING_FALLBACK_VARIABLES))
PY
)"

release_is_complete() {
  [[ -f "$CURRENT_MARKER" ]] || return 1
  python3 - "$CURRENT_MARKER" "$RUN" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(
    0 if payload.get("status") == "complete"
    and payload.get("latest_complete_run") == sys.argv[2]
    and payload.get("latest_max_forecast_hour") == 360
    and not payload.get("missing_required_variables")
    and not payload.get("missing_optional_variables")
    else 1
)
PY
}

{
  cd "$APP_DIR"
  if release_is_complete; then
    printf '%s\n' \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_OM] reuse immutable release run=$RUN"
  else
    if docker ps -a --format '{{.Names}}' | grep -Fxq weather-openmeteo-ecmwf; then
      printf '%s\n' "ECMWF producer container already exists; inspect it before retrying" >&2
      exit 1
    fi
    mkdir -p "$STAGING_DIR"
    if [[ "$(id -u)" -eq 0 ]]; then
      chown -R "${WEATHER_OPENMETEO_UID:-999}:${WEATHER_OPENMETEO_GID:-999}" "$STAGING_DIR"
    fi

    python3 scripts/probe_ecmwf_open_data_run.py --run "$RUN"
    while IFS='|' read -r source_run max_hour role; do
      if python3 scripts/ecmwf_staging_progress.py \
        --path "$PROGRESS_PATH" \
        --target-run "$RUN" \
        --is-complete "$source_run"; then
        printf '%s\n' \
          "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_OM] reuse completed role=$role run=$source_run"
        continue
      fi
      require_free_bytes "$MINIMUM_RUNTIME_FREE_BYTES" "$source_run"
      variables="$FALLBACK_VARIABLES"
      if [[ "$role" == "target" ]]; then
        variables="$RAW_VARIABLES"
      fi
      printf '%s\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_OM] download role=$role run=$source_run horizon=$max_hour"
      run_openmeteo download-ecmwf \
        --domain ifs025 \
        --run "$source_run" \
        --max-forecast-hour "$max_hour" \
        --only-variables "$variables" \
        --concurrent "${WEATHER_ECMWF_DOWNLOAD_CONCURRENT:-2}" \
        --skip-full-run
      transient="$STAGING_DIR/download-ecmwf_ifs025"
      if [[ -e "$transient" ]]; then
        transient_real="$(readlink -f -- "$transient")"
        staging_real="$(readlink -f -- "$STAGING_DIR")"
        case "$transient_real" in
          "$staging_real"/download-ecmwf_ifs025) rm -rf -- "$transient_real" ;;
          *) printf '%s\n' "Unsafe ECMWF transient directory: $transient_real" >&2; exit 1 ;;
        esac
      fi
      python3 scripts/ecmwf_staging_progress.py \
        --path "$PROGRESS_PATH" \
        --target-run "$RUN" \
        --mark-run "$source_run" >/dev/null
    done < <(
      python3 scripts/ecmwf_source_run_plan.py \
        --run "$RUN" \
        --lookback-hours "$LOOKBACK_HOURS" \
        --format lines
    )

    for forbidden in data_run http_cache download-ecmwf_ifs025; do
      [[ ! -e "$STAGING_DIR/$forbidden" ]] \
        || { printf '%s\n' "ECMWF duplicate/transient path remains: $forbidden" >&2; exit 1; }
    done
    python3 scripts/publish_ecmwf_release.py \
      --root "$ECMWF_ROOT" \
      --staging "$STAGING_DIR" \
      --run "$RUN" \
      --image "$IMAGE_REF" \
      --patch-sha256 "$PATCH_SHA256" \
      --source-revision "$SOURCE_REVISION"
  fi

  bash scripts/install_ecmwf_api_service.sh
  bash scripts/install_ecmwf_proxy_route.sh
  bash scripts/build_openmeteo_ecmwf_layers.sh "$RUN"
  printf '%s\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_OM] completed run=$RUN image=$IMAGE_REF api=ready webp=ready"
} 2>&1 | tee -a "$LOG_DIR/openmeteo_ecmwf_om_cycle.log"
