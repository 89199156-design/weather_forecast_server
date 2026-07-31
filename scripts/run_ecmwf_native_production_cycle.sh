#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
EXPECTED_TASK="weather_ecmwf_probe_cycle"
if [[ "${WEATHER_1PANEL_VERIFIED_TASK:-}" != "$EXPECTED_TASK" ]]; then
  printf '%s\n' "拒绝执行：ECMWF native 生产必须来自已验证的 1Panel 流水线" >&2
  exit 2
fi
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env

RUN="${1:-${WEATHER_ECMWF_RUN:-}}"
if [[ ! "$RUN" =~ ^[0-9]{10}$ || ! "${RUN:8:2}" =~ ^(00|12)$ ]]; then
  printf '%s\n' "Usage: run_ecmwf_native_production_cycle.sh YYYYMMDD{00|12}" >&2
  exit 2
fi

ECMWF_ROOT="${WEATHER_ECMWF_ROOT:-$APP_DIR/data/ecmwf}"
ECMWF_STATIC_ROOT="${WEATHER_ECMWF_STATIC_ROOT:-$APP_DIR/data/static}"
STAGING_DIR="$ECMWF_ROOT/staging/ecmwf_native_$RUN"
CURRENT_MARKER="$ECMWF_ROOT/groups/ecmwf/current/ready_for_processing.json"
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
IMAGE_NAME="${WEATHER_ECMWF_OPENMETEO_IMAGE:-weather-forecast-ecmwf}"
IMAGE_TAG="${WEATHER_ECMWF_OPENMETEO_TAG:-}"
MINIMUM_DATA_START_FREE_BYTES="${WEATHER_ECMWF_MINIMUM_START_FREE_BYTES:-10737418240}"
MINIMUM_DATA_RUNTIME_FREE_BYTES="${WEATHER_ECMWF_MINIMUM_RUNTIME_FREE_BYTES:-6442450944}"
MINIMUM_SYSTEM_FREE_BYTES="${WEATHER_SYSTEM_MINIMUM_FREE_BYTES:-10737418240}"
KEEP_COVERAGES="${WEATHER_ECMWF_KEEP_NATIVE_COVERAGES:-2}"
SOURCE_REVISION="$(git -c safe.directory="$APP_DIR" -C "$APP_DIR" rev-parse HEAD)"
PATCH_PATH="$APP_DIR/vendor/patches/open-meteo-ecmwf-regional.patch"
PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$APP_DIR/data/om_producer}"
API_MARKER="$PRODUCER_ROOT/groups/ecmwf/current/ready_for_processing.json"
WEBP_RUNNER="${WEATHER_OM_WEBP_RUNNER:-/opt/1panel/apps/weather_om_webp/scripts/run_scope.sh}"
WEBP_OUTPUT_ROOT="${WEATHER_OM_WEBP_DATA_ROOT:-/opt/1panel/apps/weather_om_webp/data}"
EXPECTED_COVERAGE_ID="ecmwf_native_${RUN}_${SOURCE_REVISION:0:12}"

[[ -n "$IMAGE_TAG" ]] || { printf '%s\n' "WEATHER_ECMWF_OPENMETEO_TAG is required" >&2; exit 2; }
[[ -f "$PATCH_PATH" ]] || { printf '%s\n' "Missing ECMWF regional source patch" >&2; exit 1; }
PATCH_SHA256="$(sha256sum "$PATCH_PATH" | awk '{print $1}')"
IMAGE_REF="$IMAGE_NAME:$IMAGE_TAG"
IMAGE_LABELS="$(docker image inspect "$IMAGE_REF" --format '{{json .Config.Labels}}')"
PYTHONPATH="$APP_DIR/scripts${PYTHONPATH:+:$PYTHONPATH}" python3 - \
  "$IMAGE_LABELS" "$PATCH_SHA256" "$SOURCE_REVISION" <<'PY'
import json
import sys

from ecmwf_contract import OPENMETEO_UPSTREAM_COMMIT

labels = json.loads(sys.argv[1]) or {}
expected = {
    "io.weather-forecast.component": "ecmwf-native-engine",
    "io.weather-forecast.openmeteo-upstream-commit": OPENMETEO_UPSTREAM_COMMIT,
    "io.weather-forecast.ecmwf-patch-sha256": sys.argv[2],
    "io.weather-forecast.ecmwf-source-id": sys.argv[3],
}
mismatches = {
    key: {"expected": value, "actual": labels.get(key)}
    for key, value in expected.items()
    if labels.get(key) != value
}
if mismatches:
    raise SystemExit(f"ECMWF native image provenance mismatch: {mismatches}")
PY

available_bytes() {
  df -PB1 -- "$1" | awk 'NR == 2 {print $4}'
}

require_free_bytes() {
  local path="$1"
  local required="$2"
  local stage="$3"
  local available
  available="$(available_bytes "$path")"
  if [[ ! "$available" =~ ^[0-9]+$ || "$available" -lt "$required" ]]; then
    printf '%s\n' \
      "ECMWF disk preflight failed stage=$stage path=$path available=$available required=$required" >&2
    exit 1
  fi
}

remove_scoped_path() {
  local target="$1"
  local expected_parent="$2"
  [[ -e "$target" ]] || return 0
  local target_real
  local parent_real
  target_real="$(readlink -f -- "$target")"
  parent_real="$(readlink -f -- "$expected_parent")"
  if [[ "$(dirname -- "$target_real")" != "$parent_real" ]]; then
    printf '%s\n' "unsafe ECMWF cleanup path: $target_real" >&2
    exit 1
  fi
  rm -rf -- "$target_real"
}

validate_native_run() {
  local domain="$1"
  local source_run="$2"
  local horizon="$3"
  local variables="$4"
  PYTHONPATH="$APP_DIR/scripts${PYTHONPATH:+:$PYTHONPATH}" python3 - \
    "$STAGING_DIR" "$domain" "$source_run" "$horizon" "$variables" <<'PY'
from pathlib import Path
import sys

from publish_ecmwf_native_coverage import validate_run

validate_run(
    Path(sys.argv[1]),
    sys.argv[2],
    sys.argv[3],
    int(sys.argv[4]),
    {value for value in sys.argv[5].split(",") if value},
)
PY
}

mkdir -p "$ECMWF_ROOT/staging" "$LOG_DIR"
require_free_bytes "$ECMWF_ROOT" "$MINIMUM_DATA_START_FREE_BYTES" data-start
require_free_bytes "$APP_DIR" "$MINIMUM_SYSTEM_FREE_BYTES" system-reserve

for peer in gfs cams cams-ads ecmwf; do
  if docker ps --format '{{.Names}}' | grep -Fxq "weather-openmeteo-$peer"; then
    printf '%s\n' "ECMWF native production refuses concurrent model container: $peer" >&2
    exit 1
  fi
done

export WEATHER_OPENMETEO_IMAGE="$IMAGE_NAME"
export WEATHER_OPENMETEO_TAG="$IMAGE_TAG"
export WEATHER_OPENMETEO_DATA_DIR="$STAGING_DIR"
export WEATHER_OPENMETEO_TASK_SCOPE=ecmwf-native
export WEATHER_OPENMETEO_HTTP_CACHE_ENABLED=false
export DATA_DIRECTORY=/app/data/
export DATA_RUN_DIRECTORY=/app/data/data_run/
export CACHE_SIZE="${WEATHER_ECMWF_CACHE_SIZE:-2GB}"
export CACHE_META_SIZE="${WEATHER_ECMWF_CACHE_META_SIZE:-1MB}"
export WEATHER_ECMWF_REGIONAL_GRID=true
export WEATHER_ECMWF_STORAGE_LEFT_LON="${WEATHER_ECMWF_STORAGE_LEFT_LON:-68}"
export WEATHER_ECMWF_STORAGE_RIGHT_LON="${WEATHER_ECMWF_STORAGE_RIGHT_LON:-142}"
export WEATHER_ECMWF_STORAGE_BOTTOM_LAT="${WEATHER_ECMWF_STORAGE_BOTTOM_LAT:--2}"
export WEATHER_ECMWF_STORAGE_TOP_LAT="${WEATHER_ECMWF_STORAGE_TOP_LAT:-60}"

{
  cd "$APP_DIR"
  REUSE_PUBLISHED=false
  if [[ -f "$CURRENT_MARKER" ]] && python3 - \
    "$CURRENT_MARKER" "$RUN" "$EXPECTED_COVERAGE_ID" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
products = payload.get("products") or {}
raise SystemExit(
    0 if payload.get("status") == "complete"
    and payload.get("runtime_format") == "openmeteo-native-v1"
    and payload.get("latest_complete_run") == sys.argv[2]
    and payload.get("coverage_id") == sys.argv[3]
    and {"ecmwf_ifs025", "ecmwf_ifs025_ensemble"} <= set(products)
    else 1
)
PY
  then
    REUSE_PUBLISHED=true
    printf '%s\n' \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_NATIVE] reuse published immutable OM run=$RUN coverage=$EXPECTED_COVERAGE_ID"
  fi

  if [[ "$REUSE_PUBLISHED" != "true" ]]; then
    if [[ -f "$CURRENT_MARKER" ]] \
    && python3 - "$CURRENT_MARKER" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if payload.get("runtime_format") == "openmeteo-native-v1" else 1)
PY
  then
    if [[ ! -e "$STAGING_DIR" ]]; then
      current_coverage="$(python3 - "$ECMWF_ROOT" "$CURRENT_MARKER" <<'PY'
from pathlib import Path, PurePosixPath
import json
import sys
root = Path(sys.argv[1]).resolve()
payload = json.load(open(sys.argv[2], encoding="utf-8"))
relative = PurePosixPath(str(payload["coverage_path"]))
if relative.is_absolute() or ".." in relative.parts or relative.parts[:2] != ("coverages", "ecmwf"):
    raise SystemExit("unsafe ECMWF native coverage_path")
coverage = (root / Path(*relative.parts)).resolve(strict=True)
if coverage.parent != (root / "coverages" / "ecmwf").resolve(strict=True):
    raise SystemExit("ECMWF native coverage resolves outside managed root")
print(coverage)
PY
)"
      cp -al -- "$current_coverage" "$STAGING_DIR"
      rm -f -- "$STAGING_DIR/coverage.json"
      previous_patch_sha256="$(python3 - "$CURRENT_MARKER" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("regional_patch_sha256") or "")
PY
)"
      if [[ "$previous_patch_sha256" != "$PATCH_SHA256" ]]; then
        remove_scoped_path \
          "$STAGING_DIR/data_run/ecmwf_ifs025" \
          "$STAGING_DIR/data_run"
        printf '%s\n' \
          "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_NATIVE] deterministic OM will be rebuilt because producer patch changed"
      fi
    fi
  else
    mkdir -p "$STAGING_DIR"
  fi

    if [[ "$(id -u)" -eq 0 ]]; then
      find "$STAGING_DIR" -type d -exec chown "${WEATHER_OPENMETEO_UID:-999}:${WEATHER_OPENMETEO_GID:-999}" {} +
    fi
    python3 scripts/ensure_ecmwf_static_asset.py --root "$ECMWF_STATIC_ROOT"
    export WEATHER_OPENMETEO_STATIC_ROOT="$ECMWF_STATIC_ROOT"
    export WEATHER_ECMWF_OFFICIAL_HSURF=/app/static/ecmwf_ifs025/HSURF.om
    openmeteo_set_runtime_defaults
    write_sanitized_env_file
    trap 'rm -f "${SANITIZED_ENV_FILE:-}"' EXIT

    while IFS='|' read -r source_run max_hour role; do
      RUN_VARIABLES="$(PYTHONPATH="$APP_DIR/scripts" python3 - "$max_hour" <<'PY'
import sys
from ecmwf_contract import raw_variables_for_horizon
print(",".join(raw_variables_for_horizon(int(sys.argv[1]))))
PY
)"
      if validate_native_run ecmwf_ifs025 "$source_run" "$max_hour" "$RUN_VARIABLES"; then
        printf '%s\n' \
          "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_NATIVE] reuse deterministic role=$role run=$source_run"
        continue
      fi
      remove_scoped_path \
        "$STAGING_DIR/data_run/ecmwf_ifs025/${source_run:0:4}/${source_run:4:2}/${source_run:6:2}/${source_run:8:2}00Z" \
        "$STAGING_DIR/data_run/ecmwf_ifs025/${source_run:0:4}/${source_run:4:2}/${source_run:6:2}"
      require_free_bytes "$ECMWF_ROOT" "$MINIMUM_DATA_RUNTIME_FREE_BYTES" "deterministic-$source_run"
      python3 scripts/probe_ecmwf_open_data_run.py \
        --run "$source_run" \
        --max-forecast-hour "$max_hour"
      run_openmeteo download-ecmwf \
        --domain ifs025 \
        --run "$source_run" \
        --max-forecast-hour "$max_hour" \
        --only-variables "$RUN_VARIABLES" \
        --concurrent "${WEATHER_ECMWF_DOWNLOAD_CONCURRENT:-2}" \
        --skip-timeseries
      remove_scoped_path "$STAGING_DIR/download-ecmwf_ifs025" "$STAGING_DIR"
      validate_native_run ecmwf_ifs025 "$source_run" "$max_hour" "$RUN_VARIABLES"
    done < <(python3 scripts/ecmwf_source_run_plan.py --run "$RUN" --format lines)

    while IFS='|' read -r source_run max_hour; do
      if validate_native_run ecmwf_ifs025_ensemble "$source_run" "$max_hour" precipitation_probability; then
        printf '%s\n' \
          "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_NATIVE] reuse probability run=$source_run"
        continue
      fi
      remove_scoped_path \
        "$STAGING_DIR/data_run/ecmwf_ifs025_ensemble/${source_run:0:4}/${source_run:4:2}/${source_run:6:2}/${source_run:8:2}00Z" \
        "$STAGING_DIR/data_run/ecmwf_ifs025_ensemble/${source_run:0:4}/${source_run:4:2}/${source_run:6:2}"
      require_free_bytes "$ECMWF_ROOT" "$MINIMUM_DATA_RUNTIME_FREE_BYTES" "probability-$source_run"
      run_openmeteo download-ecmwf \
        --domain ifs025_ensemble \
        --run "$source_run" \
        --max-forecast-hour "$max_hour" \
        --only-variables precipitation \
        --concurrent "${WEATHER_ECMWF_ENSEMBLE_CONCURRENT:-2}" \
        --skip-timeseries \
        --probability-full-run-only
      remove_scoped_path "$STAGING_DIR/download-ecmwf_ifs025_ensemble" "$STAGING_DIR"
      remove_scoped_path "$STAGING_DIR/ecmwf_ifs025_ensemble" "$STAGING_DIR"
      validate_native_run ecmwf_ifs025_ensemble "$source_run" "$max_hour" precipitation_probability
    done < <(
      PYTHONPATH="$APP_DIR/scripts" python3 - "$RUN" <<'PY'
import sys
from publish_ecmwf_native_coverage import ensemble_source_run_plan
for run, horizon in ensemble_source_run_plan(sys.argv[1]):
    print(f"{run}|{horizon}")
PY
    )

    DETERMINISTIC_RUNS="$(python3 scripts/ecmwf_source_run_plan.py --run "$RUN" --format lines | cut -d'|' -f1 | paste -sd, -)"
    ENSEMBLE_RUNS="$(PYTHONPATH="$APP_DIR/scripts" python3 - "$RUN" <<'PY'
import sys
from publish_ecmwf_native_coverage import ensemble_source_run_plan
print(",".join(run for run, _ in ensemble_source_run_plan(sys.argv[1])))
PY
)"
    python3 scripts/prune_native_om_runs.py \
      --data-dir "$STAGING_DIR" \
      --domains ecmwf_ifs025 \
      --retained-runs "$DETERMINISTIC_RUNS"
    python3 scripts/prune_native_om_runs.py \
      --data-dir "$STAGING_DIR" \
      --domains ecmwf_ifs025_ensemble \
      --retained-runs "$ENSEMBLE_RUNS"

    python3 scripts/publish_ecmwf_native_coverage.py \
      --root "$ECMWF_ROOT" \
      --staging "$STAGING_DIR" \
      --run "$RUN" \
      --image "$IMAGE_REF" \
      --patch-sha256 "$PATCH_SHA256" \
      --source-revision "$SOURCE_REVISION" \
      --keep-coverages "$KEEP_COVERAGES"
  fi

  if [[ ! -f "$API_MARKER" ]] || [[ ! "$CURRENT_MARKER" -ef "$API_MARKER" ]]; then
    printf '%s\n' \
      "ECMWF native API bind is missing or does not expose the published marker: $API_MARKER" >&2
    exit 1
  fi
  [[ -x "$WEBP_RUNNER" ]] || {
    printf '%s\n' "Missing Shanghai-compatible Rust WebP runner: $WEBP_RUNNER" >&2
    exit 1
  }
  OM_DATA_ROOT="$PRODUCER_ROOT" \
    OM_WEBP_DATA_ROOT="$WEBP_OUTPUT_ROOT" \
    OM_STRICT_DATA_ROOT="${WEATHER_OM_STRICT_DATA_ROOT:-/srv/weather-data}" \
    OM_OMFILE_LIB="${WEATHER_OMFILE_LIB:-/opt/1panel/apps/weather_om_api/native/libomfileformat.so}" \
    OM_DEM_ROOT="${WEATHER_OM_DEM_ROOT:-$APP_DIR/data/point}" \
    OM_MODEL_STATIC_ROOT="${WEATHER_OM_MODEL_STATIC_ROOT:-/opt/1panel/apps/weather_om_api}" \
    OM_WEBP_PUBLIC_ROOT="${WEATHER_OM_WEBP_PUBLIC_ROOT:-/opt/1panel/apps/weather/data}" \
    OM_WEBP_WORKERS="${WEATHER_OM_WEBP_WORKERS:-1}" \
    bash "$WEBP_RUNNER" ecmwf

  bash "$APP_DIR/scripts/reload_native_api_snapshot.sh" \
    ecmwf "$EXPECTED_COVERAGE_ID"

  printf '%s\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ECMWF_NATIVE] completed run=$RUN image=$IMAGE_REF coverage=$EXPECTED_COVERAGE_ID WebP=rust API=rust"
} 2>&1 | tee -a "$LOG_DIR/openmeteo_ecmwf_native_cycle.log"
