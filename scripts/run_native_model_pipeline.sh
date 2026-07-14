#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env

SCOPE="${1:-}"
RUN="${2:-}"
if [[ "$SCOPE" != "gfs" && "$SCOPE" != "cams" ]] || [[ ! "$RUN" =~ ^[0-9]{10}$ ]]; then
  printf '%s\n' "Usage: run_native_model_pipeline.sh [gfs|cams] YYYYMMDDHH" >&2
  exit 2
fi

PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"
PIPELINE_LOCK="${WEATHER_OM_PIPELINE_LOCK_FILE:-/tmp/weather_native_model_pipeline.lock}"
WEBP_BIN="${WEATHER_OM_WEBP_BIN:-/opt/1panel/apps/weather_om_webp/bin/om-webp}"
WEBP_OUTPUT_ROOT="${WEATHER_OM_WEBP_DATA_ROOT:-/opt/1panel/apps/weather_om_webp/data}"
OMFILE_LIB="${WEATHER_OMFILE_LIB:-/opt/1panel/apps/weather_om_api/native/libomfileformat.so}"
API_SERVICE="${WEATHER_OM_API_SERVICE:-weather-om-api.service}"

published_run() {
  python3 - "$PRODUCER_ROOT/groups/$SCOPE/current/ready_for_processing.json" <<'PY'
import json
import sys
from pathlib import Path

marker = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if marker.get("status") != "complete" or marker.get("runtime_format") != "openmeteo-native-v1":
    raise SystemExit("native marker is not complete")
print(marker.get("latest_complete_run", ""))
PY
}

{
  flock -n 9 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [NATIVE_PIPELINE] another model pipeline is still running; skip scope=$SCOPE run=$RUN"
    exit 0
  }

  cd "$APP_DIR"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [NATIVE_PIPELINE] start scope=$SCOPE run=$RUN"
  if [[ "$SCOPE" == "gfs" ]]; then
    WEATHER_GFS_RUN="$RUN" bash scripts/run_gfs_om_production_cycle.sh "$RUN"
  else
    WEATHER_CAMS_RUN="$RUN" bash scripts/run_cams_om_production_cycle.sh "$RUN"
  fi

  actual_run="$(published_run)"
  if [[ "$actual_run" != "$RUN" ]]; then
    printf '%s\n' "Published $SCOPE run $actual_run does not match requested run $RUN" >&2
    exit 1
  fi

  if [[ ! -x "$WEBP_BIN" ]]; then
    printf '%s\n' "Missing Rust WebP renderer: $WEBP_BIN" >&2
    exit 1
  fi
  if [[ ! -f "$OMFILE_LIB" ]]; then
    printf '%s\n' "Missing OM decoder library: $OMFILE_LIB" >&2
    exit 1
  fi
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [NATIVE_PIPELINE] OM complete; render WebP scope=$SCOPE run=$RUN"
  nice -n 10 ionice -c 2 -n 7 "$WEBP_BIN" \
    --scope "$SCOPE" \
    --data-root "$PRODUCER_ROOT" \
    --output-root "$WEBP_OUTPUT_ROOT" \
    --decoder-lib "$OMFILE_LIB"

  if ! systemctl is-active --quiet "$API_SERVICE"; then
    printf '%s\n' "Rust API service is not active: $API_SERVICE" >&2
    exit 1
  fi
  systemctl reload "$API_SERVICE"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [NATIVE_PIPELINE] complete scope=$SCOPE run=$RUN; API refresh signalled once"
} 9>"$PIPELINE_LOCK"
