from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_1panel_v1_task_state import (
    MINIMUM_RETAIN_COPIES,
    PRODUCTION_TASKS,
    RECOMMENDED_RETAIN_COPIES,
    all_panel_tasks_idle,
    decision,
    production_tasks_idle,
)


def make_database(path: Path, active: dict[str, int]) -> dict[str, list[Path]]:
    logs: dict[str, list[Path]] = {name: [] for name in PRODUCTION_TASKS}
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "create table cronjobs ("
            "id integer primary key, name text, status text, retain_copies integer)"
        )
        connection.execute(
            "create table job_records ("
            "id integer primary key, cronjob_id integer, status text, "
            "start_time text, records text)"
        )
        for task_id, name in enumerate(PRODUCTION_TASKS, start=1):
            connection.execute(
                "insert into cronjobs (id, name, status, retain_copies) "
                "values (?, ?, 'Enable', ?)",
                (task_id, name, RECOMMENDED_RETAIN_COPIES),
            )
            for index in range(active.get(name, 0)):
                log_path = path.parent / f"{name}-{index}.log"
                log_path.touch()
                logs[name].append(log_path)
                connection.execute(
                    "insert into job_records "
                    "(id, cronjob_id, status, start_time, records) "
                    "values (?, ?, 'Waiting', ?, ?)",
                    (
                        task_id * 100 + index,
                        task_id,
                        f"2026-07-23 00:{index:02d}:00",
                        str(log_path),
                    ),
                )
        connection.commit()
    return logs


def test_exactly_one_active_record_is_the_current_invocation(tmp_path: Path) -> None:
    database = tmp_path / "1Panel.db"
    logs = make_database(database, {PRODUCTION_TASKS[0]: 1})
    action, reason = decision(database, PRODUCTION_TASKS[0], logs[PRODUCTION_TASKS[0]][0])
    assert action == "run"
    assert "当前记录" in reason


def test_older_active_self_record_skips_new_invocation(tmp_path: Path) -> None:
    database = tmp_path / "1Panel.db"
    logs = make_database(database, {PRODUCTION_TASKS[0]: 2})
    action, reason = decision(database, PRODUCTION_TASKS[0], logs[PRODUCTION_TASKS[0]][1])
    assert action == "skip"
    assert "100" in reason


def test_older_invocation_still_wins_if_new_record_arrives_before_its_check(
    tmp_path: Path,
) -> None:
    database = tmp_path / "1Panel.db"
    logs = make_database(database, {PRODUCTION_TASKS[0]: 2})
    action, reason = decision(database, PRODUCTION_TASKS[0], logs[PRODUCTION_TASKS[0]][0])
    assert action == "run"
    assert "当前记录 100" in reason


def test_other_tasks_are_deliberately_ignored(tmp_path: Path) -> None:
    database = tmp_path / "1Panel.db"
    logs = make_database(
        database,
        {PRODUCTION_TASKS[0]: 1, PRODUCTION_TASKS[1]: 3, PRODUCTION_TASKS[2]: 2},
    )
    assert decision(database, PRODUCTION_TASKS[0], logs[PRODUCTION_TASKS[0]][0])[0] == "run"


def test_manual_launch_without_panel_record_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "1Panel.db"
    make_database(database, {})
    with pytest.raises(RuntimeError, match="non-production/manual launch"):
        decision(database, PRODUCTION_TASKS[0], tmp_path / "manual.log")


def test_wrong_current_log_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "1Panel.db"
    make_database(database, {PRODUCTION_TASKS[0]: 1})
    with pytest.raises(RuntimeError, match="not uniquely identifiable"):
        decision(database, PRODUCTION_TASKS[0], tmp_path / "wrong.log")


def test_production_idle_gate_ignores_unrelated_panel_tasks(tmp_path: Path) -> None:
    database = tmp_path / "1Panel.db"
    make_database(database, {})
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "insert into cronjobs (id, name, status, retain_copies) "
            "values (99, 'unrelated', 'Enable', 1)"
        )
        connection.execute(
            "insert into job_records (id, cronjob_id, status, start_time, records) "
            "values (999, 99, 'Waiting', '2026-07-23 00:00:00', '/tmp/unrelated.log')"
        )
        connection.commit()
    assert production_tasks_idle(database)[0]
    assert not all_panel_tasks_idle(database)[0]


def test_production_idle_gate_reports_active_weather_task(tmp_path: Path) -> None:
    database = tmp_path / "1Panel.db"
    make_database(database, {PRODUCTION_TASKS[2]: 1})
    idle, reason = production_tasks_idle(database)
    assert not idle
    assert PRODUCTION_TASKS[2] in reason


def test_unsupported_task_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "1Panel.db"
    make_database(database, {})
    with pytest.raises(ValueError, match="unsupported production task"):
        decision(database, "weather_unknown", tmp_path / "unknown.log")


def test_unsafe_record_retention_is_rejected_before_a_task_can_run(
    tmp_path: Path,
) -> None:
    database = tmp_path / "1Panel.db"
    logs = make_database(database, {PRODUCTION_TASKS[0]: 1})
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "update cronjobs set retain_copies = ? where name = ?",
            (MINIMUM_RETAIN_COPIES - 1, PRODUCTION_TASKS[0]),
        )
        connection.commit()
    with pytest.raises(RuntimeError, match="log retention is unsafe"):
        decision(database, PRODUCTION_TASKS[0], logs[PRODUCTION_TASKS[0]][0])


def test_recommended_retention_covers_full_1panel_timeout_window() -> None:
    assert MINIMUM_RETAIN_COPIES == 73
    assert RECOMMENDED_RETAIN_COPIES >= MINIMUM_RETAIN_COPIES


def test_idle_deployment_check_allows_operator_disabled_tasks(tmp_path: Path) -> None:
    database = tmp_path / "1Panel.db"
    make_database(database, {})
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("update cronjobs set status = 'Disable', retain_copies = 1")
        connection.commit()
    assert production_tasks_idle(database)[0]


def test_disabled_task_cannot_start_production_work(tmp_path: Path) -> None:
    database = tmp_path / "1Panel.db"
    logs = make_database(database, {PRODUCTION_TASKS[0]: 1})
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "update cronjobs set status = 'Disable' where name = ?",
            (PRODUCTION_TASKS[0],),
        )
        connection.commit()
    with pytest.raises(RuntimeError, match="not enabled"):
        decision(database, PRODUCTION_TASKS[0], logs[PRODUCTION_TASKS[0]][0])
