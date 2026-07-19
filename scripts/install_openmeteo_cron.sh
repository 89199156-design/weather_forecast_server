#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
RUNTIME_ROOT="${WEATHER_FORECAST_RUNTIME_ROOT:-/opt/1panel/apps/weather_forecast_server}"
ENV_FILE="${WEATHER_OPENMETEO_ENV_FILE:-$RUNTIME_ROOT/config/singapore.private.env}"
PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$RUNTIME_ROOT/data/om_producer}"
PANEL_DB="${WEATHER_1PANEL_DB:-/opt/1panel/db/1Panel.db}"
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
  printf '%s\n' "python3 is required to install 1Panel weather cronjobs." >&2
  exit 2
fi

export PANEL_DB APP_DIR ENV_FILE PRODUCER_ROOT

PYTHON_RUNNER=(python3)
if [[ "$(id -u)" -ne 0 ]]; then
  PYTHON_RUNNER=(sudo --preserve-env=PANEL_DB,APP_DIR,ENV_FILE,PRODUCER_ROOT python3)
fi

"${PYTHON_RUNNER[@]}" <<'PY'
import os
import sqlite3

panel_db = os.environ["PANEL_DB"]
app_dir = os.environ["APP_DIR"]
env_file = os.environ["ENV_FILE"]
producer_root = os.environ["PRODUCER_ROOT"]

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
    tasks = (
        (
            "weather_gfs_probe_cycle",
            # 1Panel v1 uses commas to separate complete cron expressions.
            # Do not use a standard comma-separated hour field here: the
            # scheduler would split it into invalid fragments at startup.
            "17 0 * * *,17 6 * * *,17 12 * * *,17 18 * * *",
            "\n".join(
                (
                    "#!/bin/bash",
                    "set -euo pipefail",
                    f"export WEATHER_FORECAST_APP_DIR={app_dir}",
                    f"export WEATHER_OPENMETEO_ENV_FILE={env_file}",
                    f"export WEATHER_OM_PRODUCER_ROOT={producer_root}",
                    f"exec /usr/bin/nice -n 15 /usr/bin/ionice -c 3 /bin/bash {app_dir}/scripts/run_gfs_probe_and_cycle.sh",
                    "",
                )
            ),
        ),
        (
            "weather_cams_ftp_probe_cycle",
            "37 4 * * *,37 16 * * *",
            "\n".join(
                (
                    "#!/bin/bash",
                    "set -euo pipefail",
                    f"export WEATHER_FORECAST_APP_DIR={app_dir}",
                    f"export WEATHER_OPENMETEO_ENV_FILE={env_file}",
                    f"export WEATHER_OM_PRODUCER_ROOT={producer_root}",
                    f"exec /usr/bin/nice -n 15 /usr/bin/ionice -c 3 /bin/bash {app_dir}/scripts/run_cams_ftp_scheduled_cycle.sh",
                    "",
                )
            ),
        ),
    )
    conn.executemany(
        """
        INSERT INTO cronjobs
            (created_at, updated_at, name, type, spec, script, retain_copies, status)
        VALUES (CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, 'shell', ?, ?, 7, 'Enable')
        """,
        tasks,
    )
    conn.commit()
PY

# 1Panel is the sole scheduler. Remove the exact legacy host cron after the
# two enabled 1Panel rows are committed; it is deliberately not retained.
if [[ -f "$SYSTEM_CRON_FILE" ]]; then
  "${SUDO[@]}" rm -f -- "$SYSTEM_CRON_FILE"
fi

# Restart only the control panel so it registers the newly committed database
# rows. The weather API and active model pipeline remain untouched.
if command -v systemctl >/dev/null 2>&1 \
  && "${SUDO[@]}" systemctl is-active --quiet "$PANEL_SERVICE"; then
  "${SUDO[@]}" systemctl restart "$PANEL_SERVICE"
fi
printf '%s\n' "Installed enabled 1Panel Open-Meteo cronjobs: weather_gfs_probe_cycle, weather_cams_ftp_probe_cycle"
