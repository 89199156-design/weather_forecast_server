#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
RUNTIME_ROOT="${WEATHER_FORECAST_RUNTIME_ROOT:-/opt/1panel/apps/weather_forecast_server}"
ENV_FILE="${WEATHER_OPENMETEO_ENV_FILE:-$RUNTIME_ROOT/config/singapore.private.env}"
PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$RUNTIME_ROOT/data/om_producer}"
PANEL_DB="${WEATHER_1PANEL_DB:-/opt/1panel/db/1Panel.db}"
PANEL_DB_BACKUP_DIR="${WEATHER_1PANEL_DB_BACKUP_DIR:-/opt/1panel/db}"
PANEL_SERVICE="${WEATHER_1PANEL_SERVICE:-1panel.service}"
SYSTEM_CRON_FILE="${WEATHER_OPENMETEO_CRON_FILE:-/etc/cron.d/weather-openmeteo}"
SUDO=()
if [[ "$(id -u)" -ne 0 ]]; then
  SUDO=(sudo)
fi

if [[ ! -f "$PANEL_DB" ]]; then
  printf '%s\n' "1Panel database not found: $PANEL_DB" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' "python3 is required to retire legacy 1Panel cronjobs." >&2
  exit 2
fi

backup_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_file="$PANEL_DB_BACKUP_DIR/1Panel.db.weather_openmeteo_cron_backup_$backup_stamp"
"${SUDO[@]}" cp "$PANEL_DB" "$backup_file"

if [[ -f "$SYSTEM_CRON_FILE" ]]; then
  "${SUDO[@]}" cp "$SYSTEM_CRON_FILE" "$PANEL_DB_BACKUP_DIR/weather-openmeteo.cron_backup_$backup_stamp"
fi

export PANEL_DB

PYTHON_RUNNER=(python3)
if [[ "$(id -u)" -ne 0 ]]; then
  PYTHON_RUNNER=(sudo --preserve-env=PANEL_DB python3)
fi

"${PYTHON_RUNNER[@]}" <<'PY'
import os
import sqlite3

panel_db = os.environ["PANEL_DB"]

with sqlite3.connect(panel_db) as conn:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        DELETE FROM cronjobs
        WHERE name LIKE 'weather_%'
           OR name LIKE 'openmeteo_%'
           OR name IN ('OM_GFS_WEBP_BUILD', 'OM_CAMS_WEBP_BUILD')
        """
    )
    conn.commit()
PY

# Direct database inserts do not register entry_ids with every 1Panel release.
# Use the host cron daemon as the single real scheduler and keep the 1Panel
# database free of duplicate weather jobs. The Singapore host uses UTC+8 local
# time. Probe once per upstream model cycle, after the full horizon is normally
# ready: GFS 00/06/12/18 UTC + 4h17m, CAMS 00/12 UTC + 8h37m.
cron_tmp="$(mktemp)"
trap 'rm -f "$cron_tmp"' EXIT
printf '%s\n' \
  'SHELL=/bin/bash' \
  'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
  "17 0,6,12,18 * * * root /usr/bin/env WEATHER_FORECAST_APP_DIR=$APP_DIR WEATHER_OPENMETEO_ENV_FILE=$ENV_FILE WEATHER_OM_PRODUCER_ROOT=$PRODUCER_ROOT /usr/bin/nice -n 15 /usr/bin/ionice -c 3 /bin/bash $APP_DIR/scripts/run_gfs_probe_and_cycle.sh" \
  "37 4,16 * * * root /usr/bin/env WEATHER_FORECAST_APP_DIR=$APP_DIR WEATHER_OPENMETEO_ENV_FILE=$ENV_FILE WEATHER_OM_PRODUCER_ROOT=$PRODUCER_ROOT /usr/bin/nice -n 15 /usr/bin/ionice -c 3 /bin/bash $APP_DIR/scripts/run_cams_ftp_scheduled_cycle.sh" \
  > "$cron_tmp"
"${SUDO[@]}" install -o root -g root -m 0644 "$cron_tmp" "$SYSTEM_CRON_FILE"

# Retired 1Panel schedules may remain in its in-memory scheduler until reload.
# Restart only the control panel; the weather API and active model pipeline are
# separate services/processes and remain untouched.
if command -v systemctl >/dev/null 2>&1 \
  && "${SUDO[@]}" systemctl is-active --quiet "$PANEL_SERVICE"; then
  "${SUDO[@]}" systemctl restart "$PANEL_SERVICE"
fi
if command -v systemctl >/dev/null 2>&1 \
  && "${SUDO[@]}" systemctl is-active --quiet cron.service; then
  "${SUDO[@]}" systemctl reload cron.service
fi

printf '%s\n' "Installed system Open-Meteo cronjobs: $SYSTEM_CRON_FILE"
printf '%s\n' "Backup: $backup_file"
