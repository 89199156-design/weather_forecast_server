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

if [[ -n "${WEATHER_ECMWF_REFERENCE_RUN:-}" ]]; then
  RUN="$WEATHER_ECMWF_REFERENCE_RUN"
else
  RUN="$(date -u -d '8 hours ago' '+%Y%m%d00')"
fi

cd "$APP_DIR"
if ! python3 scripts/probe_ecmwf_open_data_run.py --run "$RUN"; then
  printf '%s\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_PROBE] incomplete run=$RUN; no download started"
  exit 0
fi

CURRENT_MARKER="${WEATHER_ECMWF_ROOT:-$APP_DIR/data/ecmwf}/groups/ecmwf/current/ready_for_processing.json"
WEBP_MARKER="${WEATHER_OM_WEBP_DATA_ROOT:-/opt/1panel/apps/weather_om_webp/data}/current/ecmwf.json"
API_PORT="${WEATHER_ECMWF_API_PORT:-18081}"
if [[ -f "$CURRENT_MARKER" && -f "$WEBP_MARKER" ]] && python3 - "$CURRENT_MARKER" "$WEBP_MARKER" "$RUN" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
webp = json.load(open(sys.argv[2], encoding="utf-8"))
raise SystemExit(
    0 if payload.get("status") == "complete"
    and payload.get("latest_complete_run") == sys.argv[3]
    and payload.get("latest_max_forecast_hour") == 360
    and not payload.get("missing_required_variables")
    and not payload.get("missing_optional_variables")
    and webp.get("status") == "complete"
    and webp.get("run") == sys.argv[3]
    and webp.get("layer_count") == 16
    else 1
)
PY
then
  if ! curl --fail --silent --show-error \
    --header 'Host: api.open-meteo.com' \
    "http://127.0.0.1:$API_PORT/v1/ecmwf?latitude=31.23&longitude=121.47&hourly=temperature_2m&forecast_days=1" \
    >/dev/null; then
    printf '%s\n' \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_PROBE] API repair required run=$RUN"
  else
    printf '%s\n' \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_PROBE] skip complete run=$RUN"
    exit 0
  fi
fi

exec bash "$APP_DIR/scripts/run_ecmwf_om_production_cycle.sh" "$RUN"
