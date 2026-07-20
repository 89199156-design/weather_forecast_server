#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env

GROUP="${1:-}"
COVERAGE_ID="${2:-}"
if [[ ! "$GROUP" =~ ^[a-z][a-z0-9_]{0,31}$ ]] \
  || [[ ! "$COVERAGE_ID" =~ ^[a-zA-Z0-9_-]{1,160}$ ]]; then
  printf '%s\n' "Usage: reload_native_api_snapshot.sh GROUP COVERAGE_ID" >&2
  exit 2
fi

PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"
API_SERVICE="${WEATHER_OM_API_SERVICE:-weather-om-api.service}"
TIMEOUT_SECONDS="${WEATHER_OM_API_RELOAD_CONFIRM_TIMEOUT_SECONDS:-60}"
SUCCESS_EVENT="published new immutable OM API snapshot"
SUCCESS_IDENTITY="| $GROUP=$COVERAGE_ID |"
if ! [[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  printf '%s\n' "WEATHER_OM_API_RELOAD_CONFIRM_TIMEOUT_SECONDS must be positive" >&2
  exit 2
fi
if ! systemctl is-active --quiet "$API_SERVICE"; then
  printf '%s\n' "Rust API service is not active: $API_SERVICE" >&2
  exit 1
fi

cursor_output="$(journalctl --unit "$API_SERVICE" --lines 0 --show-cursor --no-pager)"
cursor="$(printf '%s\n' "$cursor_output" | sed -n 's/^-- cursor: //p' | tail -n 1)"
if [[ -z "$cursor" ]]; then
  printf '%s\n' "Could not capture API journal cursor" >&2
  exit 1
fi

systemctl reload "$API_SERVICE"
set +o pipefail
if timeout --signal=TERM "${TIMEOUT_SECONDS}s" \
  journalctl \
    --unit "$API_SERVICE" \
    --after-cursor="$cursor" \
    --follow \
    --no-pager \
    --output=cat \
  | awk -v event="$SUCCESS_EVENT" -v identity="$SUCCESS_IDENTITY" '
      index($0, event) && index($0, identity) { found = 1; exit }
      END { exit found ? 0 : 1 }
    ' >/dev/null; then
  confirmed=true
else
  confirmed=false
fi
set -o pipefail
if [[ "$confirmed" != "true" ]]; then
  printf '%s\n' "API reload was not confirmed for group=$GROUP coverage=$COVERAGE_ID" >&2
  exit 1
fi

python3 - "$PRODUCER_ROOT" "$GROUP" "$COVERAGE_ID" <<'PY'
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

root = Path(sys.argv[1])
group = sys.argv[2]
coverage_id = sys.argv[3]
path = root / "groups" / group / "applied" / "current.json"
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
temporary.write_text(
    json.dumps(
        {
            "status": "applied",
            "coverage_id": coverage_id,
            "applied_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    + "\n",
    encoding="utf-8",
)
os.replace(temporary, path)
PY

echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [NATIVE_API] applied group=$GROUP coverage=$COVERAGE_ID"
