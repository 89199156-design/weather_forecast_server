#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
CRON_FILE="${WEATHER_OPENMETEO_CRON_FILE:-/etc/cron.d/weather-openmeteo}"
CRON_USER="${WEATHER_OPENMETEO_CRON_USER:-root}"
LOG_DIR="${WEATHER_OPENMETEO_BUILD_LOG_DIR:-/opt/1panel/apps/weather/logs}"

tmp_file="$(mktemp)"
cleanup() {
  rm -f "$tmp_file"
}
trap cleanup EXIT

cat >"$tmp_file" <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CRON_TZ=UTC

*/20 * * * * $CRON_USER cd $APP_DIR && bash scripts/run_gfs_probe_and_cycle.sh
*/20 * * * * $CRON_USER cd $APP_DIR && bash scripts/run_cams_ftp_scheduled_cycle.sh
0 10,22 * * * $CRON_USER cd $APP_DIR && bash scripts/run_cams_ads_scheduled_cycle.sh
EOF

install -d -m 0755 "$(dirname "$CRON_FILE")"
install -m 0644 "$tmp_file" "$CRON_FILE"
mkdir -p "$LOG_DIR"
