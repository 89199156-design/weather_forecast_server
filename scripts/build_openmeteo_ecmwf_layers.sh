#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env

RUN="${1:-${WEATHER_ECMWF_RUN:-}}"
if [[ ! "$RUN" =~ ^[0-9]{10}$ || "${RUN:8:2}" != "00" ]]; then
  printf '%s\n' "Usage: build_openmeteo_ecmwf_layers.sh YYYYMMDD00" >&2
  exit 2
fi

WEBP_ROOT="${WEATHER_OM_WEBP_DATA_ROOT:-/opt/1panel/apps/weather_om_webp/data}"
PUBLIC_DATA_DIR="${WEATHER_OPENMETEO_PUBLIC_DATA_DIR:-$APP_DIR/data/public}"
API_URL="${WEATHER_OPENMETEO_ECMWF_API_URL:-http://127.0.0.1:18081/v1/ecmwf}"
MODEL="${WEATHER_OPENMETEO_ECMWF_LAYER_MODEL:-ecmwf_ifs025}"
FRAME_COUNT="${WEATHER_OPENMETEO_ECMWF_LAYER_FRAME_COUNT:-121}"
CHUNK_SIZE="${WEATHER_OPENMETEO_ECMWF_LAYER_CHUNK_SIZE:-250}"
TIMEOUT="${WEATHER_OPENMETEO_ECMWF_LAYER_TIMEOUT:-180}"
REQUEST_RETRIES="${WEATHER_OPENMETEO_ECMWF_LAYER_REQUEST_RETRIES:-2}"
REQUEST_RETRY_DELAY="${WEATHER_OPENMETEO_ECMWF_LAYER_REQUEST_RETRY_DELAY:-2}"
RELEASE_ID="ecmwf_ifs025_$RUN"
RELEASE_ROOT="$WEBP_ROOT/releases/$RELEASE_ID"
STAGING_ROOT="$WEBP_ROOT/staging/${RELEASE_ID}_$$"
PRODUCT_DIR="$RELEASE_ROOT/ecmwf_ifs025"
MARKER="$WEBP_ROOT/current/ecmwf.json"

if [[ ! "$FRAME_COUNT" =~ ^[0-9]+$ || "$FRAME_COUNT" -lt 1 || "$FRAME_COUNT" -gt 361 ]]; then
  printf '%s\n' "Invalid ECMWF WebP frame count: $FRAME_COUNT" >&2
  exit 2
fi

RUN_HOUR="$(python3 - "$RUN" <<'PY'
from datetime import datetime, timezone
import sys
value = datetime.strptime(sys.argv[1], "%Y%m%d%H").replace(tzinfo=timezone.utc)
print(value.strftime("%Y-%m-%dT%H:00"))
PY
)"
END_HOUR="$(python3 - "$RUN_HOUR" "$FRAME_COUNT" <<'PY'
from datetime import datetime, timedelta, timezone
import sys
value = datetime.strptime(sys.argv[1], "%Y-%m-%dT%H:00").replace(tzinfo=timezone.utc)
print((value + timedelta(hours=int(sys.argv[2]) - 1)).strftime("%Y-%m-%dT%H:00"))
PY
)"

marker_matches() {
  [[ -f "$MARKER" && -d "$PRODUCT_DIR" ]] || return 1
  python3 - "$MARKER" "$RUN" "$RELEASE_ID" "$FRAME_COUNT" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(
    0 if payload.get("status") == "complete"
    and payload.get("run") == sys.argv[2]
    and payload.get("release_id") == sys.argv[3]
    and payload.get("frame_count") == int(sys.argv[4])
    else 1
)
PY
}

if marker_matches; then
  printf '%s\n' "ECMWF WebP release already complete run=$RUN release=$RELEASE_ID"
  exit 0
fi
if [[ -e "$RELEASE_ROOT" ]]; then
  printf '%s\n' "ECMWF WebP immutable release is incomplete: $RELEASE_ROOT" >&2
  exit 1
fi
if [[ -e "$STAGING_ROOT" ]]; then
  printf '%s\n' "Unexpected ECMWF WebP staging path: $STAGING_ROOT" >&2
  exit 1
fi

curl --fail --silent --show-error \
  "$API_URL?latitude=31.23&longitude=121.47&hourly=temperature_2m&models=$MODEL&start_hour=$RUN_HOUR&end_hour=$RUN_HOUR" \
  >/dev/null

mkdir -p "$STAGING_ROOT/ecmwf_ifs025" "$WEBP_ROOT/releases" \
  "$WEBP_ROOT/current" "$PUBLIC_DATA_DIR/webp"
cleanup() {
  if [[ -d "$STAGING_ROOT" ]]; then
    rm -rf -- "$STAGING_ROOT"
  fi
}
trap cleanup EXIT

python3 "$APP_DIR/scripts/build_webp.py" \
  --scope ecmwf \
  --api-base-url "$API_URL" \
  --output-dir "$STAGING_ROOT/ecmwf_ifs025" \
  --model "$MODEL" \
  --start-hour "$RUN_HOUR" \
  --end-hour "$END_HOUR" \
  --chunk-size "$CHUNK_SIZE" \
  --timeout-seconds "$TIMEOUT" \
  --request-retries "$REQUEST_RETRIES" \
  --request-retry-delay "$REQUEST_RETRY_DELAY"

python3 - "$STAGING_ROOT" "$RUN" "$RELEASE_ID" "$FRAME_COUNT" <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
run = sys.argv[2]
release_id = sys.argv[3]
frame_count = int(sys.argv[4])
manifest = json.loads(
    (root / "ecmwf_ifs025" / "ecmwf_ifs025_data.json").read_text(
        encoding="utf-8"
    )
)
if (
    manifest.get("source") != "ecmwf"
    or manifest.get("frame_count") != frame_count
    or manifest.get("grid", {}).get("width") != 281
    or manifest.get("grid", {}).get("height") != 233
):
    raise SystemExit("ECMWF WebP manifest does not satisfy the product contract")
payload = {
    "version": 1,
    "status": "complete",
    "scope": "ecmwf",
    "run": run,
    "release_id": release_id,
    "frame_count": frame_count,
    "layer_count": 16,
    "product_path": f"releases/{release_id}/ecmwf_ifs025",
    "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
(root / "ready_for_processing.json").write_text(
    json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

mv -T "$STAGING_ROOT" "$RELEASE_ROOT"
trap - EXIT

tmp_marker="$WEBP_ROOT/current/.ecmwf.$$.tmp"
cp "$RELEASE_ROOT/ready_for_processing.json" "$tmp_marker"
mv -T "$tmp_marker" "$MARKER"

tmp_link="$PUBLIC_DATA_DIR/webp/.ecmwf_ifs025.$$.tmp"
ln -s "$PRODUCT_DIR" "$tmp_link"
if [[ -e "$PUBLIC_DATA_DIR/webp/ecmwf_ifs025" && ! -L "$PUBLIC_DATA_DIR/webp/ecmwf_ifs025" ]]; then
  rm -f -- "$tmp_link"
  printf '%s\n' "Refusing to replace non-symlink ECMWF WebP public path" >&2
  exit 1
fi
mv -Tf "$tmp_link" "$PUBLIC_DATA_DIR/webp/ecmwf_ifs025"
cp -f "$APP_DIR/config/weather_layer_catalog.json" \
  "$PUBLIC_DATA_DIR/webp/weather_layer_catalog.json"

printf '%s\n' \
  "ECMWF WebP complete run=$RUN frames=$FRAME_COUNT layers=16 release=$RELEASE_ID"
