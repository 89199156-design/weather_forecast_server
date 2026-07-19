#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"
SHANGHAI_URL="${WEATHER_SHANGHAI_OM_API_URL:-}"
SINGAPORE_URL="${WEATHER_SINGAPORE_OM_API_URL:-http://127.0.0.1:8088}"
API_PID="${WEATHER_OM_API_PID:-}"
HOURLY_RUN_IDENTITY_REPORT="${WEATHER_OM_RUN_IDENTITY_REPORT:-}"
DAILY_RUN_IDENTITY_REPORT="${WEATHER_OM_DAILY_RUN_IDENTITY_REPORT:-}"
SHANGHAI_WEBP_INVENTORY="${WEATHER_SHANGHAI_WEBP_INVENTORY:-}"
REPORT_ROOT="${WEATHER_OM_PARITY_REPORT_ROOT:-$PRODUCER_ROOT/reports}"
WEBP_OUTPUT_ROOT="${WEATHER_OM_WEBP_DATA_ROOT:-/opt/1panel/apps/weather_om_webp/data}"

if [[ -z "$SHANGHAI_URL" ]]; then
  printf '%s\n' "WEATHER_SHANGHAI_OM_API_URL is required." >&2
  exit 2
fi
if [[ -z "$HOURLY_RUN_IDENTITY_REPORT" || ! -f "$HOURLY_RUN_IDENTITY_REPORT" ]]; then
  printf '%s\n' "WEATHER_OM_RUN_IDENTITY_REPORT must point to a passed Shanghai/Singapore identity report." >&2
  exit 2
fi
if [[ -z "$DAILY_RUN_IDENTITY_REPORT" || ! -f "$DAILY_RUN_IDENTITY_REPORT" ]]; then
  printf '%s\n' "WEATHER_OM_DAILY_RUN_IDENTITY_REPORT must point to a fresh identity report collected after the hourly comparison." >&2
  exit 2
fi
if [[ "$DAILY_RUN_IDENTITY_REPORT" -ef "$HOURLY_RUN_IDENTITY_REPORT" ]]; then
  printf '%s\n' "Hourly and daily comparisons require distinct identity reports; collect the daily report after hourly completion." >&2
  exit 2
fi
if [[ ! "$API_PID" =~ ^[1-9][0-9]*$ || ! -d "/proc/$API_PID/fd" ]]; then
  printf '%s\n' "WEATHER_OM_API_PID must identify the running Singapore API process." >&2
  exit 2
fi
if [[ -z "$SHANGHAI_WEBP_INVENTORY" || ! -f "$SHANGHAI_WEBP_INVENTORY" ]]; then
  printf '%s\n' "WEATHER_SHANGHAI_WEBP_INVENTORY must point to the strict Shanghai WebP inventory." >&2
  exit 2
fi
for group in gfs cams; do
  if [[ ! -L "$PRODUCER_ROOT/current/$group" || ! -f "$PRODUCER_ROOT/groups/$group/current/ready_for_processing.json" ]]; then
    printf '%s\n' "Missing complete native $group coverage under $PRODUCER_ROOT" >&2
    exit 2
  fi
done

read_marker_run() {
  python3 - "$1" <<'PY'
import json
import sys
from pathlib import Path

marker = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if marker.get("status") != "complete" or marker.get("runtime_format") != "openmeteo-native-v1":
    raise SystemExit("marker is not a complete native coverage")
run = str(marker.get("latest_complete_run") or "")
if len(run) != 10 or not run.isdigit():
    raise SystemExit("marker has invalid latest_complete_run")
print(run)
PY
}

GFS_RUN="$(read_marker_run "$PRODUCER_ROOT/groups/gfs/current/ready_for_processing.json")"
CAMS_RUN="$(read_marker_run "$PRODUCER_ROOT/groups/cams/current/ready_for_processing.json")"
mkdir -p "$REPORT_ROOT"

if ! curl --silent --show-error --fail --max-time 10 \
  "$SINGAPORE_URL/v1/forecast?latitude=31.2&longitude=121.5&hourly=temperature_2m&forecast_hours=1" \
  >/dev/null; then
  printf '%s\n' "Running Singapore Rust API is unavailable: $SINGAPORE_URL" >&2
  exit 1
fi

python3 "$APP_DIR/scripts/validate_native_om_coverage.py" \
  --producer-root "$PRODUCER_ROOT" \
  --api-base-url "$SINGAPORE_URL" \
  --output-report "$REPORT_ROOT/gfs_native_candidate.json"
python3 "$APP_DIR/scripts/validate_native_cams_coverage.py" \
  --producer-root "$PRODUCER_ROOT" \
  --api-base-url "$SINGAPORE_URL" \
  --output-report "$REPORT_ROOT/cams_native_candidate.json"
python3 "$APP_DIR/scripts/compare_model_run_identities.py" inventory \
  --data-root "$PRODUCER_ROOT" \
  --process-pid "$API_PID" \
  --output "$REPORT_ROOT/singapore-model-run-identity.json"
python3 "$APP_DIR/scripts/compare_shanghai_singapore_api.py" \
  --shanghai-url "$SHANGHAI_URL" \
  --singapore-url "$SINGAPORE_URL" \
  --gfs-run "$GFS_RUN" \
  --cams-run "$CAMS_RUN" \
  --run-identity-report "$HOURLY_RUN_IDENTITY_REPORT" \
  --output-report "$REPORT_ROOT/shanghai-singapore-2000-all-hours.json"
python3 "$APP_DIR/scripts/compare_shanghai_singapore_daily.py" \
  --shanghai-url "$SHANGHAI_URL" \
  --singapore-url "$SINGAPORE_URL" \
  --gfs-run "$GFS_RUN" \
  --cams-run "$CAMS_RUN" \
  --run-identity-report "$DAILY_RUN_IDENTITY_REPORT" \
  --hourly-acceptance-report "$REPORT_ROOT/shanghai-singapore-2000-all-hours.json" \
  --output-report "$REPORT_ROOT/shanghai-singapore-2000x3-daily.json"
nice -n 15 ionice -c 3 python3 "$APP_DIR/scripts/compare_webp_inventories.py" inventory \
  --output-root "$WEBP_OUTPUT_ROOT" \
  --output "$REPORT_ROOT/singapore-webp-inventory.json"
python3 "$APP_DIR/scripts/compare_webp_inventories.py" compare \
  --shanghai-inventory "$SHANGHAI_WEBP_INVENTORY" \
  --singapore-inventory "$REPORT_ROOT/singapore-webp-inventory.json" \
  --output-report "$REPORT_ROOT/shanghai-singapore-webp-exact.json"
