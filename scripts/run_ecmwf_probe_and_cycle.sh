#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
EXPECTED_TASK="weather_ecmwf_probe_cycle"
if [[ "${WEATHER_1PANEL_VERIFIED_TASK:-}" != "$EXPECTED_TASK" ]]; then
  printf '%s\n' "拒绝执行：ECMWF 探测必须来自已验证的 1Panel 任务" >&2
  exit 2
fi
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env

ECMWF_ROOT="${WEATHER_ECMWF_ROOT:-$APP_DIR/data/ecmwf}"
cd "$APP_DIR"
SOURCE_REVISION="$(git -c safe.directory="$APP_DIR" -C "$APP_DIR" rev-parse HEAD)"
if [[ -n "${WEATHER_ECMWF_REFERENCE_RUN:-}" ]]; then
  RUN="$WEATHER_ECMWF_REFERENCE_RUN"
  if ! python3 scripts/probe_ecmwf_open_data_run.py --run "$RUN"; then
    printf '%s\n' \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_PROBE] incomplete run=$RUN; no download started"
    exit 0
  fi
else
  probe_output=""
  if ! probe_output="$(python3 scripts/probe_ecmwf_open_data_run.py \
    --latest-ready \
    --data-root "$ECMWF_ROOT" 2>&1)"; then
    printf '%s\n' \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_PROBE] $probe_output"
    exit 0
  fi
  ready_line="$(printf '%s\n' "$probe_output" | awk '$1 == "READY" && $2 != "" { print; exit }')"
  if [[ -z "$ready_line" ]]; then
    printf '%s\n' \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_PROBE] no complete long run found"
    exit 0
  fi
  set -- $ready_line
  RUN="$2"
fi
EXPECTED_COVERAGE_ID="ecmwf_native_${RUN}_${SOURCE_REVISION:0:12}"

CURRENT_MARKER="$ECMWF_ROOT/groups/ecmwf/current/ready_for_processing.json"
WEBP_MARKER="${WEATHER_OM_WEBP_DATA_ROOT:-/opt/1panel/apps/weather_om_webp/data}/current/ecmwf_ifs025.json"
API_PORT="${WEATHER_OM_API_PORT:-8088}"
if [[ -f "$CURRENT_MARKER" && -f "$WEBP_MARKER" ]] && python3 - \
  "$CURRENT_MARKER" "$WEBP_MARKER" "$RUN" "$EXPECTED_COVERAGE_ID" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
webp = json.load(open(sys.argv[2], encoding="utf-8"))
products = payload.get("products") or {}
raise SystemExit(
    0 if payload.get("status") == "complete"
    and payload.get("runtime_format") == "openmeteo-native-v1"
    and payload.get("latest_complete_run") == sys.argv[3]
    and payload.get("coverage_id") == sys.argv[4]
    and payload.get("native_producer_contract") == 2
    and {"ecmwf_ifs025", "ecmwf_ifs025_ensemble"} <= set(products)
    and webp.get("status") == "complete"
    and webp.get("scope") == "ecmwf_ifs025"
    and webp.get("run") == sys.argv[3]
    and webp.get("contract_version") == 2
    and len(webp.get("layers") or []) == 18
    else 1
)
PY
then
  if ! curl --fail --silent --show-error \
    "http://127.0.0.1:$API_PORT/v1/ecmwf?latitude=31.23&longitude=121.47&hourly=temperature_2m,precipitation_probability&forecast_days=1" \
    >/dev/null; then
    printf '%s\n' \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_PROBE] API repair required run=$RUN"
  else
    printf '%s\n' \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_PROBE] skip complete run=$RUN"
    exit 0
  fi
fi

exec bash "$APP_DIR/scripts/run_ecmwf_native_production_cycle.sh" "$RUN"
