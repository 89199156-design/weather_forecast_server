#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
RUNTIME_ROOT="${WEATHER_FORECAST_RUNTIME_ROOT:-/opt/1panel/apps/weather_forecast_server}"
ENV_FILE="${WEATHER_OPENMETEO_ENV_FILE:-$RUNTIME_ROOT/config/singapore.private.env}"
PRODUCER_ROOT="${WEATHER_OM_PRODUCER_ROOT:-$RUNTIME_ROOT/data/om_producer}"
TASK_RETAIN_COPIES="${WEATHER_1PANEL_TASK_RETAIN_COPIES:-100}"
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
if [[ ! -f "$APP_DIR/scripts/check_1panel_v1_task_state.py" ]]; then
  printf '%s\n' "1Panel task-state checker not found in production source: $APP_DIR" >&2
  exit 2
fi

require_safe_panel_restart_window() {
  local seconds_in_five_minute_slot
  seconds_in_five_minute_slot=$(( $(date +%s) % 300 ))
  if (( seconds_in_five_minute_slot < 15 || seconds_in_five_minute_slot > 240 )); then
    printf '%s\n' \
      "Refusing to modify/restart 1Panel within 60 seconds of a production-task trigger; retry in the safe window." >&2
    return 75
  fi
}

PANEL_WAS_ACTIVE=false
if command -v systemctl >/dev/null 2>&1 \
  && "${SUDO[@]}" systemctl is-active --quiet "$PANEL_SERVICE"; then
  PANEL_WAS_ACTIVE=true
  # Check before any database or legacy-cron mutation so an unsafe time never
  # leaves a committed but not-yet-loaded half installation.
  require_safe_panel_restart_window
fi

export PANEL_DB APP_DIR ENV_FILE PRODUCER_ROOT TASK_RETAIN_COPIES

PYTHON_RUNNER=(python3)
if [[ "$(id -u)" -ne 0 ]]; then
  PYTHON_RUNNER=(sudo --preserve-env=PANEL_DB,APP_DIR,ENV_FILE,PRODUCER_ROOT,TASK_RETAIN_COPIES python3)
fi

"${PYTHON_RUNNER[@]}" <<'PY'
import os
import shlex
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.environ["APP_DIR"], "scripts"))
from check_1panel_v1_task_state import (  # noqa: E402
    MINIMUM_RETAIN_COPIES,
    RECOMMENDED_RETAIN_COPIES,
)

panel_db = os.environ["PANEL_DB"]
app_dir = os.environ["APP_DIR"]
env_file = os.environ["ENV_FILE"]
producer_root = os.environ["PRODUCER_ROOT"]
retain_copies = int(os.environ["TASK_RETAIN_COPIES"])
if retain_copies < MINIMUM_RETAIN_COPIES:
    raise SystemExit(
        "WEATHER_1PANEL_TASK_RETAIN_COPIES is unsafe: "
        f"configured={retain_copies} minimum={MINIMUM_RETAIN_COPIES} "
        f"recommended={RECOMMENDED_RETAIN_COPIES}"
    )

def guarded_script(task_name: str, entrypoint: str) -> str:
    checker = os.path.join(app_dir, "scripts", "check_1panel_v1_task_state.py")
    check_command = " ".join(
        (
            "/usr/bin/python3",
            shlex.quote(checker),
            "--database",
            shlex.quote(panel_db),
            "--current-task",
            shlex.quote(task_name),
        )
    )
    command = (
        "/usr/bin/nice -n 15 /usr/bin/ionice -c 3 /bin/bash "
        + shlex.quote(os.path.join(app_dir, entrypoint))
    )
    return "\n".join(
        (
            "#!/bin/bash",
            "set -euo pipefail",
            'CURRENT_LOG_PATH="$(readlink -f "/proc/$$/fd/1")"',
            f'TASK_STATE=$({check_command} --current-log-path "$CURRENT_LOG_PATH")',
            'case "$TASK_STATE" in',
            f"  run\\|*) export WEATHER_1PANEL_VERIFIED_TASK={shlex.quote(task_name)}; printf '%s\\n' \"检查｜任务：{task_name}｜状态：允许执行｜${{TASK_STATE#run|}}\" ;;",
            f"  skip\\|*) printf '%s\\n' \"跳过｜任务：{task_name}｜原因：${{TASK_STATE#skip|}}\"; exit 0 ;;",
            f"  *) printf '%s\\n' \"失败｜任务：{task_name}｜原因：未知任务状态 $TASK_STATE\" >&2; exit 2 ;;",
            "esac",
            f"export WEATHER_FORECAST_APP_DIR={shlex.quote(app_dir)}",
            f"export WEATHER_OPENMETEO_ENV_FILE={shlex.quote(env_file)}",
            f"export WEATHER_OM_PRODUCER_ROOT={shlex.quote(producer_root)}",
            f"exec {command}",
            "",
        )
    )


tasks = (
        (
            "weather_gfs_probe_cycle",
            # 1Panel v1 uses commas to separate complete cron expressions.
            # Do not use a standard comma-separated hour field here: the
            # scheduler would split it into invalid fragments at startup.
            # Probe every twenty minutes. The first shell action queries this
            # task's own 1Panel records and skips a duplicate invocation.
            "0 * * * *,20 * * * *,40 * * * *",
            "scripts/run_gfs_probe_and_cycle.sh",
        ),
        (
            "weather_cams_ecpds_probe_cycle",
            # ECPDS probes are cheap HEAD requests and become no-ops when the
            # local three-run window is complete. Offset them from GFS.
            "5 * * * *,25 * * * *,45 * * * *",
            "scripts/run_cams_ftp_scheduled_cycle.sh",
        ),
        (
            "weather_cams_ads_cycle",
            # ADS never probes remote availability. This frequent local check
            # reacts only after ECPDS publishes a new UTC date. Its own 1Panel
            # active record covers the POST, queue, download and publication.
            "10 * * * *,30 * * * *,50 * * * *",
            "scripts/run_cams_ads_scheduled_cycle.sh",
        ),
    )

task_names = tuple(task[0] for task in tasks)
with sqlite3.connect(panel_db) as conn:
    conn.execute("BEGIN IMMEDIATE")
    active = list(
        conn.execute(
            "select r.id, c.name, r.start_time from job_records r "
            "join cronjobs c on c.id = r.cronjob_id "
            "where r.status in ('Running', 'Waiting') order by r.id"
        )
    )
    if active:
        details = ", ".join(
            f"{record_id}:{name}@{start_time}"
            for record_id, name, start_time in active
        )
        raise SystemExit(
            "refusing to reinstall 1Panel tasks while any panel task is active: "
            + details
        )

    placeholders = ",".join("?" for _ in task_names)
    conn.execute(
        f"""
        DELETE FROM cronjobs
        WHERE (
               name LIKE 'weather_%'
            OR name LIKE 'openmeteo_%'
            OR name IN ('OM_GFS_WEBP_BUILD', 'OM_CAMS_WEBP_BUILD')
        )
          AND name NOT IN ({placeholders})
        """,
        task_names,
    )
    for name, spec, entrypoint in tasks:
        rows = list(
            conn.execute("select id from cronjobs where name = ? order by id", (name,))
        )
        if len(rows) > 1:
            ids = ",".join(str(row[0]) for row in rows)
            raise SystemExit(f"duplicate 1Panel task name {name}: ids={ids}")
        script = guarded_script(name, entrypoint)
        if rows:
            conn.execute(
                """
                UPDATE cronjobs
                SET updated_at = CURRENT_TIMESTAMP,
                    type = 'shell', spec = ?, script = ?,
                    retain_copies = CASE
                        WHEN retain_copies < ? THEN ? ELSE retain_copies END
                WHERE id = ?
                """,
                (spec, script, retain_copies, retain_copies, int(rows[0][0])),
            )
        else:
            conn.execute(
                """
                INSERT INTO cronjobs
                    (created_at, updated_at, name, type, spec, script, retain_copies, status)
                VALUES (CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, 'shell', ?, ?, ?, 'Disable')
                """,
                (name, spec, script, retain_copies),
            )
    for name in task_names:
        count, installed_retention = conn.execute(
            "select count(*), min(retain_copies) from cronjobs where name = ?", (name,)
        ).fetchone()
        if count != 1:
            raise SystemExit(f"expected exactly one installed 1Panel task {name}, got {count}")
        if int(installed_retention) < MINIMUM_RETAIN_COPIES:
            raise SystemExit(
                f"installed 1Panel task retention is unsafe: {name} "
                f"retain_copies={installed_retention} minimum={MINIMUM_RETAIN_COPIES}"
            )
    conn.commit()
PY

# 1Panel is the sole scheduler. Remove the exact legacy host cron after the
# three 1Panel rows are committed; it is deliberately not retained. Existing
# enable/disable states are preserved, and a missing task is created disabled.
if [[ -f "$SYSTEM_CRON_FILE" ]]; then
  "${SUDO[@]}" rm -f -- "$SYSTEM_CRON_FILE"
fi

# Restart only the control panel so it registers the newly committed database
# rows. Every installed task falls on a five-minute boundary. Refuse the edge
# of that boundary, then recheck every 1Panel task immediately before restart;
# this prevents a scheduler tick from appearing between the database update
# and the control-panel restart.
if [[ "$PANEL_WAS_ACTIVE" == true ]] \
  && "${SUDO[@]}" systemctl is-active --quiet "$PANEL_SERVICE"; then
  require_safe_panel_restart_window
  "${SUDO[@]}" python3 "$APP_DIR/scripts/check_1panel_v1_task_state.py" \
    --database "$PANEL_DB" \
    --require-all-idle
  "${SUDO[@]}" systemctl restart "$PANEL_SERVICE"
fi
printf '%s\n' "Installed 1Panel Open-Meteo cronjobs while preserving existing enable/disable states: weather_gfs_probe_cycle, weather_cams_ecpds_probe_cycle, weather_cams_ads_cycle"
