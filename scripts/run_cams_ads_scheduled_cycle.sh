#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"
LOCK_FILE="${WEATHER_OPENMETEO_CAMS_ADS_SCHEDULE_LOCK_FILE:-/tmp/weather_openmeteo_cams_ads_schedule.lock}"
CYCLE_LOCK_FILE="${WEATHER_OPENMETEO_CAMS_ADS_LOCK_FILE:-/tmp/weather_openmeteo_cams_ads_cycle.lock}"
GLOBAL_LOCK_FILE="${WEATHER_OPENMETEO_GLOBAL_LOCK_FILE:-/tmp/weather_openmeteo_production.lock}"

mkdir -p "$LOG_DIR"

{
  flock -n 9 || {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS_SCHEDULE] previous schedule check still running, skip."
    exit 0
  }

  cd "$APP_DIR"
  source scripts/openmeteo_runtime_common.sh
  load_weather_env

  if [[ "${WEATHER_GIT_PULL:-false}" == "true" ]]; then
    git pull --ff-only
  fi

  {
    flock -n 7 || {
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS_SCHEDULE] another Open-Meteo production cycle is running, skip."
      exit 0
    }
  } 7>"$GLOBAL_LOCK_FILE"

  run="$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone

now = datetime.now(timezone.utc)
if now.hour >= 22:
    target = now.replace(hour=12, minute=0, second=0, microsecond=0)
elif now.hour >= 10:
    target = now.replace(hour=0, minute=0, second=0, microsecond=0)
else:
    previous = now - timedelta(days=1)
    target = previous.replace(hour=12, minute=0, second=0, microsecond=0)
print(target.strftime("%Y%m%d%H"))
PY
)"
  greenhouse_run="$(python3 - "$run" <<'PY'
from datetime import datetime, timezone
import sys

run = datetime.strptime(sys.argv[1], "%Y%m%d%H").replace(tzinfo=timezone.utc)
print(run.replace(hour=0).strftime("%Y%m%d%H"))
PY
)"

  data_dir="${WEATHER_OPENMETEO_DATA_DIR:-$APP_DIR/data/openmeteo}"
  if python3 scripts/validate_openmeteo_latest_run.py --data-dir "$data_dir" --run "$run" --domains cams_global --min-frames 121 >/dev/null 2>&1 && \
     python3 scripts/validate_openmeteo_latest_run.py --data-dir "$data_dir" --run "$greenhouse_run" --domains cams_global_greenhouse_gases --min-frames 41 >/dev/null 2>&1; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS_SCHEDULE] run=$run already current"
    exit 0
  fi

  {
    flock -n 8 || {
      echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS_SCHEDULE] CAMS ADS/CDS production cycle already running, skip."
      exit 0
    }
  } 8>"$CYCLE_LOCK_FILE"

  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [OPENMETEO_CAMS_ADS_SCHEDULE] start run=$run"
  WEATHER_CAMS_RUN="$run" bash scripts/run_cams_ads_production_cycle.sh "$run"
} 9>"$LOCK_FILE" >> "$LOG_DIR/openmeteo_cams_ads_schedule.log" 2>&1
