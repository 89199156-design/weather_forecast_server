#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
case "${1:-}" in
  gfs) EXPECTED_PANEL_TASK="weather_gfs_probe_cycle" ;;
  cams) EXPECTED_PANEL_TASK="weather_cams_ecpds_probe_cycle" ;;
  *) EXPECTED_PANEL_TASK="" ;;
esac
if [[ -n "$EXPECTED_PANEL_TASK" && "${WEATHER_1PANEL_VERIFIED_TASK:-}" != "$EXPECTED_PANEL_TASK" ]]; then
  printf '%s\n' "拒绝执行：原生模型生产流水线缺少对应的 1Panel 任务验证" >&2
  exit 2
fi
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env

SCOPE="${1:-}"
RUN="${2:-}"
MODE="${3:-produce}"
if [[ "$SCOPE" != "gfs" && "$SCOPE" != "cams" ]] \
  || [[ ! "$RUN" =~ ^[0-9]{10}$ ]] \
  || [[ "$MODE" != "produce" && "$MODE" != "apply-published" ]]; then
  printf '%s\n' \
    "Usage: run_native_model_pipeline.sh [gfs|cams] YYYYMMDDHH [produce|apply-published]" >&2
  exit 2
fi

PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"
WEBP_BIN="${WEATHER_OM_WEBP_BIN:-/opt/1panel/apps/weather_om_webp/bin/om-webp}"
WEBP_OUTPUT_ROOT="${WEATHER_OM_WEBP_DATA_ROOT:-/opt/1panel/apps/weather_om_webp/data}"
WEBP_PUBLIC_ROOT="${WEATHER_OM_WEBP_PUBLIC_ROOT:-/opt/1panel/apps/weather/data}"
WEBP_WORKERS="${WEATHER_OM_WEBP_WORKERS:-1}"
WEBP_NOFILE_LIMIT="${WEATHER_OM_WEBP_NOFILE_LIMIT:-65536}"
OMFILE_LIB="${WEATHER_OMFILE_LIB:-/opt/1panel/apps/weather_om_api/native/libomfileformat.so}"

published_identity() {
  python3 - "$PRODUCER_ROOT/groups/$SCOPE/current/ready_for_processing.json" <<'PY'
import json
import sys
from pathlib import Path

marker = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if marker.get("status") != "complete" or marker.get("runtime_format") != "openmeteo-native-v1":
    raise SystemExit("native marker is not complete")
run = marker.get("latest_complete_run", "")
coverage_id = marker.get("coverage_id", "")
if not run or not coverage_id:
    raise SystemExit("native marker identity is incomplete")
print(run, coverage_id)
PY
}

{
  cd "$APP_DIR"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [NATIVE_PIPELINE] start scope=$SCOPE run=$RUN mode=$MODE"
  if [[ "$MODE" == "produce" ]]; then
    if [[ "$SCOPE" == "gfs" ]]; then
      WEATHER_GFS_RUN="$RUN" bash scripts/run_gfs_om_production_cycle.sh "$RUN"
    else
      WEATHER_CAMS_RUN="$RUN" bash scripts/run_cams_om_production_cycle.sh "$RUN"
    fi
  else
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [NATIVE_PIPELINE] reuse already published immutable OM scope=$SCOPE run=$RUN"
  fi

  read -r actual_run actual_coverage_id < <(published_identity)
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
  if [[ ! "$WEBP_NOFILE_LIMIT" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "WEATHER_OM_WEBP_NOFILE_LIMIT must be a positive integer" >&2
    exit 2
  fi
  if ! ulimit -S -n "$WEBP_NOFILE_LIMIT"; then
    printf '%s\n' "Could not raise WebP open-file limit to $WEBP_NOFILE_LIMIT" >&2
    exit 1
  fi
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [NATIVE_PIPELINE] OM complete; render WebP scope=$SCOPE run=$RUN"
  nice -n 10 ionice -c 2 -n 7 "$WEBP_BIN" \
    --scope "$SCOPE" \
    --data-root "$PRODUCER_ROOT" \
    --output-root "$WEBP_OUTPUT_ROOT" \
    --public-root "$WEBP_PUBLIC_ROOT" \
    --decoder-lib "$OMFILE_LIB" \
    --workers "$WEBP_WORKERS"

  bash "$APP_DIR/scripts/reload_native_api_snapshot.sh" "$SCOPE" "$actual_coverage_id"
  python3 "$APP_DIR/scripts/prune_native_coverage_history.py" \
    --producer-root "$PRODUCER_ROOT" \
    --scope "$SCOPE" \
    --expected-coverage-id "$actual_coverage_id"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [NATIVE_PIPELINE] complete scope=$SCOPE run=$RUN; API refresh confirmed and old coverage pruned"
}
