#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
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
  printf '%s\n' "python3 is required to install 1Panel cronjobs." >&2
  exit 2
fi

backup_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_file="$PANEL_DB_BACKUP_DIR/1Panel.db.weather_openmeteo_cron_backup_$backup_stamp"
"${SUDO[@]}" cp "$PANEL_DB" "$backup_file"

if [[ -f "$SYSTEM_CRON_FILE" ]]; then
  "${SUDO[@]}" cp "$SYSTEM_CRON_FILE" "$PANEL_DB_BACKUP_DIR/weather-openmeteo.cron_backup_$backup_stamp"
  "${SUDO[@]}" rm -f "$SYSTEM_CRON_FILE"
fi

gfs_script=$(cat <<EOF
#!/bin/bash
set -euo pipefail
cd $APP_DIR
exec nice -n 15 ionice -c 3 bash scripts/run_gfs_probe_and_cycle.sh
EOF
)

cams_ftp_script=$(cat <<EOF
#!/bin/bash
set -euo pipefail
cd $APP_DIR
exec nice -n 15 ionice -c 3 bash scripts/run_cams_ftp_scheduled_cycle.sh
EOF
)

export GFS_SCRIPT="$gfs_script"
export CAMS_FTP_SCRIPT="$cams_ftp_script"
export PANEL_DB

PYTHON_RUNNER=(python3)
if [[ "$(id -u)" -ne 0 ]]; then
  PYTHON_RUNNER=(sudo --preserve-env=GFS_SCRIPT,CAMS_FTP_SCRIPT,PANEL_DB python3)
fi

"${PYTHON_RUNNER[@]}" <<'PY'
import os
import sqlite3

panel_db = os.environ["PANEL_DB"]
gfs_script = os.environ["GFS_SCRIPT"].replace("\r", "")
cams_ftp_script = os.environ["CAMS_FTP_SCRIPT"].replace("\r", "")

row_sql = """
INSERT INTO cronjobs (
  created_at, updated_at, name, type, spec, command, container_name, script,
  website, app_id, db_type, db_name, url, source_dir, exclusion_rules,
  keep_local, target_dir_id, backup_accounts, default_download, retain_copies,
  status, entry_ids, secret
) VALUES (
  datetime('now','localtime'), datetime('now','localtime'),
  ?, 'shell', ?, NULL, NULL, ?,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, NULL, 10,
  'Enable', NULL, NULL
)
"""

with sqlite3.connect(panel_db) as conn:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM cronjobs WHERE name LIKE 'weather_%' OR name LIKE 'openmeteo_%'")
    # The Singapore host and 1Panel scheduler use UTC+8 local time. Probe once per
    # upstream model cycle, after the complete forecast horizon is normally ready:
    # GFS 00/06/12/18 UTC + 4h17m, CAMS 00/12 UTC + 8h37m.
    conn.execute(row_sql, ("openmeteo_gfs_probe_cycle", "17 0,6,12,18 * * *", gfs_script))
    conn.execute(row_sql, ("openmeteo_cams_ftp_probe_cycle", "37 4,16 * * *", cams_ftp_script))
    conn.commit()
PY

# 1Panel keeps cron schedules in memory. Reload it after the database
# transaction so retired high-frequency jobs cannot continue until a later
# machine reboot. This restarts only the control panel, not the weather API or
# any model-production service.
if command -v systemctl >/dev/null 2>&1 \
  && "${SUDO[@]}" systemctl is-active --quiet "$PANEL_SERVICE"; then
  "${SUDO[@]}" systemctl restart "$PANEL_SERVICE"
fi

printf '%s\n' "Installed 1Panel Open-Meteo cronjobs."
printf '%s\n' "Backup: $backup_file"
