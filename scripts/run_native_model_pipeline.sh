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
WEBP_PUBLIC_ROOT="${WEATHER_OM_WEBP_PUBLIC_ROOT:-/opt/1panel/apps/weather/data}"
WEBP_WORKERS="${WEATHER_OM_WEBP_WORKERS:-1}"
OMFILE_LIB="${WEATHER_OMFILE_LIB:-/opt/1panel/apps/weather_om_api/native/libomfileformat.so}"
API_SERVICE="${WEATHER_OM_API_SERVICE:-weather-om-api.service}"
API_RELOAD_CONFIRM_TIMEOUT_SECONDS="${WEATHER_OM_API_RELOAD_CONFIRM_TIMEOUT_SECONDS:-60}"
API_RELOAD_SUCCESS_EVENT="published new immutable OM API snapshot"

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
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [NATIVE_PIPELINE] OM complete; render WebP scope=$SCOPE run=$RUN"
  nice -n 10 ionice -c 2 -n 7 "$WEBP_BIN" \
    --scope "$SCOPE" \
    --data-root "$PRODUCER_ROOT" \
    --output-root "$WEBP_OUTPUT_ROOT" \
    --public-root "$WEBP_PUBLIC_ROOT" \
    --decoder-lib "$OMFILE_LIB" \
    --workers "$WEBP_WORKERS"

  if ! systemctl is-active --quiet "$API_SERVICE"; then
    printf '%s\n' "Rust API service is not active: $API_SERVICE" >&2
    exit 1
  fi
  if ! [[ "$API_RELOAD_CONFIRM_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "WEATHER_OM_API_RELOAD_CONFIRM_TIMEOUT_SECONDS must be a positive integer" >&2
    exit 2
  fi
  journal_cursor=""
  if journal_cursor_output="$(
    journalctl \
      --unit "$API_SERVICE" \
      --lines 0 \
      --show-cursor \
      --no-pager 2>/dev/null
  )"; then
    journal_cursor="$(
      printf '%s\n' "$journal_cursor_output" \
        | sed -n 's/^-- cursor: //p' \
        | tail -n 1
    )"
  fi
  systemctl reload "$API_SERVICE"
  reload_confirmed=false
  if [[ -n "$journal_cursor" ]]; then
    # grep exits after the first matching journal event, which closes the
    # journalctl follower with SIGPIPE. Temporarily disable pipefail so the
    # match, rather than that expected follower exit, decides the result.
    set +o pipefail
    if timeout --signal=TERM "${API_RELOAD_CONFIRM_TIMEOUT_SECONDS}s" \
      journalctl \
        --unit "$API_SERVICE" \
        --after-cursor="$journal_cursor" \
        --follow \
        --no-pager \
        --output=cat \
      | grep --fixed-strings --line-buffered --max-count=1 \
          "$API_RELOAD_SUCCESS_EVENT" >/dev/null; then
      reload_confirmed=true
    fi
    set -o pipefail
  fi

  if [[ "$reload_confirmed" == "true" ]]; then
    python3 "$APP_DIR/scripts/prune_native_coverage_history.py" \
      --producer-root "$PRODUCER_ROOT" \
      --scope "$SCOPE" \
      --expected-coverage-id "$actual_coverage_id"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [NATIVE_PIPELINE] complete scope=$SCOPE run=$RUN; API refresh confirmed and old coverage pruned"
  else
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [NATIVE_PIPELINE] complete scope=$SCOPE run=$RUN; API refresh signalled once but not confirmed, old coverage retained" >&2
  fi
} 9>"$PIPELINE_LOCK"
