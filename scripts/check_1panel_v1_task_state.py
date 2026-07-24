#!/usr/bin/env python3
"""Gate one 1Panel v1 cron invocation using its own active job records only."""

from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote


ACTIVE_STATUSES = ("Running", "Waiting")
PRODUCTION_TASKS = (
    "weather_gfs_probe_cycle",
    "weather_cams_ecpds_probe_cycle",
    "weather_cams_ads_cycle",
    "weather_ecmwf_probe_cycle",
)
# 1Panel v1 allows one Shell task to run for 24 hours and removes old records
# after every completed invocation without excluding a still-active record.
# These jobs trigger every 20 minutes, so at least 73 records are required to
# keep the oldest active record visible for the full timeout window.  Keep a
# wider margin in production while making the safety invariant explicit here.
PANEL_SHELL_TIMEOUT_MINUTES = 24 * 60
TASK_TRIGGER_INTERVAL_MINUTES = 20
MINIMUM_RETAIN_COPIES = (
    PANEL_SHELL_TIMEOUT_MINUTES // TASK_TRIGGER_INTERVAL_MINUTES
) + 1
RECOMMENDED_RETAIN_COPIES = 100


def _connect_read_only(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise FileNotFoundError(database)
    uri = f"file:{quote(str(database.resolve()), safe='/')}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def _task_row(
    connection: sqlite3.Connection,
    task_name: str,
    *,
    require_runnable: bool = True,
) -> tuple[int, str, int]:
    rows = list(
        connection.execute(
            "select id, status, retain_copies from cronjobs where name = ? order by id",
            (task_name,),
        )
    )
    if not rows:
        raise RuntimeError(f"1Panel task is missing: {task_name}")
    if len(rows) != 1:
        ids = ",".join(str(row[0]) for row in rows)
        raise RuntimeError(f"duplicate 1Panel tasks: {task_name} ids={ids}")
    task_id, task_status, retain_copies = (
        int(rows[0][0]),
        str(rows[0][1]),
        int(rows[0][2]),
    )
    if require_runnable and task_status != "Enable":
        raise RuntimeError(
            f"1Panel task is not enabled: {task_name} status={task_status}"
        )
    if require_runnable and retain_copies < MINIMUM_RETAIN_COPIES:
        raise RuntimeError(
            f"1Panel task log retention is unsafe: {task_name} "
            f"retain_copies={retain_copies} minimum={MINIMUM_RETAIN_COPIES}"
        )
    return task_id, task_status, retain_copies


def decision(
    database: Path, current_task: str, current_log_path: Path
) -> tuple[str, str]:
    if current_task not in PRODUCTION_TASKS:
        raise ValueError(f"unsupported production task: {current_task}")

    current_log = os.path.realpath(current_log_path)
    with closing(_connect_read_only(database)) as connection:
        task_id, _task_status, _retain_copies = _task_row(connection, current_task)
        active = list(
            connection.execute(
                "select id, status, start_time, records from job_records "
                "where cronjob_id = ? and status in (?, ?) order by id",
                (task_id, *ACTIVE_STATUSES),
            )
        )

    if not active:
        raise RuntimeError(
            f"no active 1Panel invocation record for {current_task}; "
            "refusing a non-production/manual launch"
        )

    # 1Panel v1 writes this invocation's unique log path to job_records before
    # launching bash, and that same file is the shell's stdout/stderr. Matching
    # the path identifies this invocation even if another trigger is inserted
    # before either checker runs. A plain active-row count cannot do that.
    matching = [row for row in active if os.path.realpath(str(row[3])) == current_log]
    if len(matching) != 1:
        ids = ",".join(str(row[0]) for row in matching) or "none"
        raise RuntimeError(
            f"current 1Panel record is not uniquely identifiable for {current_task}: "
            f"log={current_log} matches={ids}"
        )
    current_id = int(matching[0][0])
    prior = [int(row[0]) for row in active if int(row[0]) < current_id]
    if prior:
        prior_text = ",".join(str(record_id) for record_id in prior)
        return "skip", f"本任务已有执行实例（记录 {prior_text}）"
    return "run", f"本任务无上一执行实例（当前记录 {current_id}）"


def production_tasks_idle(database: Path) -> tuple[bool, str]:
    """Return whether all production weather tasks have no active record."""

    with closing(_connect_read_only(database)) as connection:
        task_ids: list[int] = []
        for task_name in PRODUCTION_TASKS:
            task_id, _task_status, _retain_copies = _task_row(
                connection, task_name, require_runnable=False
            )
            task_ids.append(task_id)
        placeholders = ",".join("?" for _ in task_ids)
        active = list(
            connection.execute(
                "select r.id, c.name, r.start_time, r.status "
                "from job_records r join cronjobs c on c.id = r.cronjob_id "
                f"where r.cronjob_id in ({placeholders}) "
                "and r.status in (?, ?) order by r.id",
                (*task_ids, *ACTIVE_STATUSES),
            )
        )
    if active:
        details = ", ".join(
            f"{row[1]}#{row[0]}@{row[2]}({row[3]})" for row in active
        )
        return False, details
    return True, "四个生产天气任务均为空闲"


def all_panel_tasks_idle(database: Path) -> tuple[bool, str]:
    """Return whether 1Panel has no active task of any kind before restart."""

    with closing(_connect_read_only(database)) as connection:
        active = list(
            connection.execute(
                "select r.id, c.name, r.start_time, r.status "
                "from job_records r join cronjobs c on c.id = r.cronjob_id "
                "where r.status in (?, ?) order by r.id",
                ACTIVE_STATUSES,
            )
        )
    if active:
        details = ", ".join(
            f"{row[1]}#{row[0]}@{row[2]}({row[3]})" for row in active
        )
        return False, details
    return True, "全部 1Panel 任务均为空闲"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database", type=Path, default=Path("/opt/1panel/db/1Panel.db")
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--current-task", choices=PRODUCTION_TASKS)
    mode.add_argument("--require-production-idle", action="store_true")
    mode.add_argument("--require-all-idle", action="store_true")
    parser.add_argument("--current-log-path", type=Path)
    args = parser.parse_args()
    try:
        if args.require_production_idle or args.require_all_idle:
            if args.require_all_idle:
                idle, reason = all_panel_tasks_idle(args.database)
                scope = "1Panel 任务"
            else:
                idle, reason = production_tasks_idle(args.database)
                scope = "生产天气任务"
            if not idle:
                print(f"{scope}仍在执行：{reason}", file=sys.stderr)
                return 1
            print(f"idle|{reason}")
            return 0
        if args.current_log_path is None:
            parser.error("--current-log-path is required with --current-task")
        action, reason = decision(
            args.database, args.current_task, args.current_log_path
        )
    except Exception as error:
        print(f"1Panel 自身任务状态检查失败：{error}", file=sys.stderr)
        return 2
    print(f"{action}|{reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
