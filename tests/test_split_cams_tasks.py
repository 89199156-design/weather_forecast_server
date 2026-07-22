from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plan_cams_ads_update
import cleanup_native_task_staging
import prepare_cams_ads_staging
import publish_native_cams_greenhouse_coverage as greenhouse_publisher
import validate_native_cams_greenhouse_coverage as greenhouse_validator


DOMAIN = "cams_global_greenhouse_gases"


def read_script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def lock_environment(script: str) -> dict[str, str]:
    return dict(
        re.findall(
            r'[A-Z_]*LOCK_FILE="\$\{([A-Z0-9_]+):-([^}]+)\}"',
            script,
        )
    )


def test_three_scheduled_tasks_use_panel_self_state_without_file_locks() -> None:
    tasks = {
        "gfs": read_script("run_gfs_probe_and_cycle.sh"),
        "ecpds": read_script("run_cams_ftp_scheduled_cycle.sh"),
        "ads": read_script("run_cams_ads_scheduled_cycle.sh"),
    }

    for task, script in tasks.items():
        assert lock_environment(script) == {}, task
        assert "flock" not in script, task
        assert "GLOBAL_LOCK" not in script, task
        assert "WEATHER_OPENMETEO_GLOBAL_LOCK_FILE" not in script, task
        assert "/tmp/weather_openmeteo_production.lock" not in script, task
        assert "openmeteo_task_container_state" in script, task
        assert "detached" in script, task
        assert "reconcile_native_current_pointer.py" in script, task
        assert "stage=startup cleanup" in script, task
        assert "stage=cleanup after task" in script, task
        assert "check_1panel_v1_task_state.py" in script, task
        assert "--current-log-path" in script, task
        assert "preserve container and" in script, task

    installer = read_script("install_openmeteo_cron.sh")
    assert installer.count('"weather_gfs_probe_cycle",') == 1
    assert installer.count('"weather_cams_ecpds_probe_cycle",') == 1
    assert installer.count('"weather_cams_ads_cycle",') == 1
    assert "scripts/run_gfs_probe_and_cycle.sh" in installer
    assert "scripts/run_cams_ftp_scheduled_cycle.sh" in installer
    assert "scripts/run_cams_ads_scheduled_cycle.sh" in installer
    assert "check_1panel_v1_task_state.py" in installer
    assert "--current-task" in installer
    assert "retain_copies = 7" not in installer
    assert "TASK_RETAIN_COPIES" in installer
    assert "--require-all-idle" in installer
    assert "run_native_model_pipeline.sh gfs \"$run\" apply-published" in tasks["gfs"]
    assert "run_native_model_pipeline.sh cams \"$run\" apply-published" in tasks["ecpds"]

    runtime = read_script("openmeteo_runtime_common.sh")
    assert '"No such container:"' in runtime
    assert "Cannot safely inspect task container" in runtime


@pytest.mark.parametrize(
    ("mode", "expected_rc", "expects_remove"),
    (
        ("inspect-error", 3, False),
        ("label-mismatch", 2, False),
        ("running", 4, False),
        ("stopped-rm-fail", 3, True),
        ("stopped-success", 0, True),
        ("absent", 0, False),
        ("invalid-running", 2, False),
    ),
)
def test_task_container_cleanup_is_fail_closed(
    tmp_path: Path, mode: str, expected_rc: int, expects_remove: bool
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "docker.calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "$WEATHER_TEST_DOCKER_CALLS"
if [[ "$1" == "container" && "$2" == "inspect" ]]; then
  case "$WEATHER_TEST_DOCKER_MODE" in
    inspect-error) printf '%s\n' 'daemon unavailable' >&2; exit 1 ;;
    label-mismatch) printf '%s\n' 'other|false|aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ;;
    running) printf '%s\n' 'gfs|true|aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ;;
    stopped-*) printf '%s\n' 'gfs|false|aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ;;
    absent) printf '%s\n' 'Error: No such container: weather-openmeteo-gfs' >&2; exit 1 ;;
    invalid-running) printf '%s\n' 'gfs|unknown|aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ;;
  esac
elif [[ "$1" == "rm" ]]; then
  [[ "$WEATHER_TEST_DOCKER_MODE" != "stopped-rm-fail" ]]
fi
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "WEATHER_TEST_DOCKER_CALLS": str(calls),
            "WEATHER_TEST_DOCKER_MODE": mode,
        }
    )
    command = (
        f"source {SCRIPTS / 'openmeteo_runtime_common.sh'}; "
        "set +e; cleanup_openmeteo_task_container gfs; rc=$?; "
        "printf 'rc=%s\\n' \"$rc\"; exit 0"
    )
    completed = subprocess.run(
        ["bash", "-c", command],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert f"rc={expected_rc}" in completed.stdout
    call_lines = calls.read_text(encoding="utf-8").splitlines()
    removed = any(line.startswith("rm ") for line in call_lines)
    assert removed is expects_remove


@pytest.mark.parametrize(
    "entrypoint",
    (
        "run_gfs_probe_and_cycle.sh",
        "run_cams_ftp_scheduled_cycle.sh",
        "run_cams_ads_scheduled_cycle.sh",
    ),
)
def test_unknown_container_state_never_reaches_staging_cleanup(
    tmp_path: Path, entrypoint: str
) -> None:
    app_dir = tmp_path / "app"
    scripts_dir = app_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    log_dir = tmp_path / "logs"
    producer_root = tmp_path / "producer"
    marker = tmp_path / "destructive-cleanup-called"
    (scripts_dir / "check_1panel_v1_task_state.py").write_text(
        "print('run|test invocation')\n", encoding="utf-8"
    )
    (scripts_dir / "openmeteo_runtime_common.sh").write_text(
        "load_weather_env() { :; }\n"
        "openmeteo_task_container_state() { return 3; }\n"
        f"cleanup_openmeteo_task_container() {{ touch {marker}; }}\n",
        encoding="utf-8",
    )
    (scripts_dir / "cleanup_native_task_staging.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )
    shutil.copy2(SCRIPTS / "task_progress_reporter.py", scripts_dir)
    env = os.environ.copy()
    env.update(
        {
            "WEATHER_FORECAST_APP_DIR": str(app_dir),
            "WEATHER_OPENMETEO_BUILD_LOG_DIR": str(log_dir),
            "WEATHER_OM_PRODUCER_ROOT": str(producer_root),
            "WEATHER_GIT_PULL": "false",
        }
    )
    completed = subprocess.run(
        ["bash", str(SCRIPTS / entrypoint)],
        cwd=app_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert not marker.exists(), completed.stdout + completed.stderr


def test_startup_cleanup_removes_only_exact_task_owned_staging(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    owned = producer / "staging" / "gfs_2026072006_1234"
    published = producer / "coverages" / "gfs" / "gfs_native_2026072006_v1"
    unrelated = producer / "staging" / "gfs_manual_backup"
    owned.mkdir(parents=True)
    published.mkdir(parents=True)
    unrelated.mkdir(parents=True)
    (owned / "partial.om").write_bytes(b"partial")
    (published / "coverage.json").write_text("{}", encoding="utf-8")

    result = cleanup_native_task_staging.cleanup(producer, "gfs", None)

    assert result["removed_directories"] == 1
    assert not owned.exists()
    assert published.is_dir()
    assert unrelated.is_dir()


def test_ads_cleanup_keeps_current_target_resume_and_removes_older_target(
    tmp_path: Path,
) -> None:
    producer = tmp_path / "producer"
    keep = producer / "ads_staging" / "cams_ads_2026072000"
    stale = producer / "ads_staging" / "cams_ads_2026071900"
    publish_stale = producer / "staging" / "cams_greenhouse_2026071900_42"
    for directory in (keep, stale, publish_stale):
        directory.mkdir(parents=True)

    result = cleanup_native_task_staging.cleanup(
        producer,
        "cams_ads",
        "cams_ads_2026072000",
    )

    assert result["removed_directories"] == 2
    assert keep.is_dir()
    assert not stale.exists()
    assert not publish_stale.exists()


def test_ads_first_split_update_reuses_legacy_combined_greenhouse_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = tmp_path / "producer"
    coverage = producer / "coverages" / "cams" / "legacy"
    greenhouse = coverage / DOMAIN
    greenhouse_run = coverage / "data_run" / DOMAIN
    ordinary = coverage / "cams_global"
    greenhouse.mkdir(parents=True)
    greenhouse_run.mkdir(parents=True)
    ordinary.mkdir(parents=True)
    (greenhouse / "data.om").write_bytes(b"greenhouse")
    (greenhouse_run / "latest.json").write_text("{}", encoding="utf-8")
    (ordinary / "must-not-copy.om").write_bytes(b"ordinary")
    marker = {
        "group": "cams",
        "products": {DOMAIN: {"runtime_domain": DOMAIN}},
    }
    files, bytes_total = prepare_cams_ads_staging.coverage_data_stats(coverage)
    marker["files"] = files
    marker["bytes"] = bytes_total

    def fake_current(_root: Path, group: str, **_kwargs):
        if group == "cams_greenhouse":
            return None
        assert group == "cams"
        return coverage, marker

    monkeypatch.setattr(prepare_cams_ads_staging, "safe_current_coverage", fake_current)
    staging = producer / "ads_staging" / "cams_ads_2026072000"

    result = prepare_cams_ads_staging.prepare_ads_staging(producer, staging)

    assert result["seeded_from"] == str(coverage)
    assert (staging / DOMAIN / "data.om").read_bytes() == b"greenhouse"
    assert (staging / "data_run" / DOMAIN / "latest.json").is_file()
    assert not (staging / "cams_global").exists()


def test_ads_runtime_mismatch_does_not_discard_reusable_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = tmp_path / "producer"
    coverage = producer / "coverages" / "cams_greenhouse" / "current-test"
    runtime = coverage / DOMAIN / "carbon_monoxide" / "chunk.om"
    run_data = coverage / "data_run" / DOMAIN / "2026/07/20/0000Z" / "meta.json"
    runtime.parent.mkdir(parents=True)
    run_data.parent.mkdir(parents=True)
    runtime.write_bytes(b"runtime")
    run_data.write_text("{}", encoding="utf-8")
    files, bytes_total = prepare_cams_ads_staging.coverage_data_stats(coverage)
    marker = {
        "group": "cams_greenhouse",
        "products": {DOMAIN: {"runtime_domain": DOMAIN}},
        "files": files,
        "bytes": bytes_total,
    }
    runtime.unlink()
    monkeypatch.setattr(
        prepare_cams_ads_staging,
        "safe_current_coverage",
        lambda _root, group: (coverage, marker) if group == "cams_greenhouse" else None,
    )
    staging = producer / "ads_staging" / "cams_ads_2026072000"

    result = prepare_cams_ads_staging.prepare_ads_staging(producer, staging)

    assert result == {"resumed": False, "seeded_from": str(coverage)}
    assert not (
        staging / DOMAIN / "carbon_monoxide" / "chunk.om"
    ).exists()
    assert (
        staging
        / "data_run"
        / DOMAIN
        / "2026/07/20/0000Z"
        / "meta.json"
    ).is_file()


def test_ads_plan_maps_ecpds_12z_to_same_day_00z(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plan_cams_ads_update,
        "validate_cams_contract",
        lambda _root: {
            "source_runs": ["2026071912", "2026072000", "2026072012"]
        },
    )

    def no_local_ads(_root: Path) -> dict:
        raise ValueError("no independent ADS coverage yet")

    monkeypatch.setattr(
        plan_cams_ads_update,
        "validate_greenhouse_contract",
        no_local_ads,
    )

    result = plan_cams_ads_update.plan_update(Path("unused"))

    assert result == (
        "READY 2026072012 2026072000 "
        "2026071800,2026071900,2026072000"
    )


def test_ads_plan_skips_when_same_day_00z_is_already_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = tmp_path / "producer"
    marker = (
        producer
        / "groups"
        / "cams_greenhouse"
        / "current"
        / "ready_for_processing.json"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        plan_cams_ads_update,
        "validate_cams_contract",
        lambda _root: {
            "source_runs": ["2026071912", "2026072000", "2026072012"]
        },
    )
    monkeypatch.setattr(
        plan_cams_ads_update,
        "validate_greenhouse_contract",
        lambda _root: {"latest_complete_run": "2026072000"},
    )

    result = plan_cams_ads_update.plan_update(producer)

    assert result == "SKIP ads_already_complete 2026072000"


def test_ads_plan_can_explicitly_rebuild_the_current_complete_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = tmp_path / "producer"
    marker = (
        producer
        / "groups"
        / "cams_greenhouse"
        / "current"
        / "ready_for_processing.json"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        plan_cams_ads_update,
        "validate_cams_contract",
        lambda _root: {
            "source_runs": ["2026071912", "2026072000", "2026072012"]
        },
    )
    monkeypatch.setattr(
        plan_cams_ads_update,
        "validate_greenhouse_contract",
        lambda _root: {"latest_complete_run": "2026072000"},
    )

    result = plan_cams_ads_update.plan_update(producer, force_current=True)

    assert result == (
        "READY 2026072012 2026072000 "
        "2026071800,2026071900,2026072000"
    )


def test_ads_plan_fails_closed_for_an_invalid_existing_greenhouse_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = tmp_path / "producer"
    marker = (
        producer
        / "groups"
        / "cams_greenhouse"
        / "current"
        / "ready_for_processing.json"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        plan_cams_ads_update,
        "validate_cams_contract",
        lambda _root: {
            "source_runs": ["2026071912", "2026072000", "2026072012"]
        },
    )
    monkeypatch.setattr(
        plan_cams_ads_update,
        "validate_greenhouse_contract",
        lambda _root: (_ for _ in ()).throw(ValueError("corrupt ADS marker")),
    )

    with pytest.raises(ValueError, match="corrupt ADS marker"):
        plan_cams_ads_update.plan_update(producer)


@pytest.mark.parametrize(
    ("phase", "job"),
    [
        ("submitting", None),
        (
            "submitted",
            {
                "processID": "cams-global-greenhouse-gas-forecasts",
                "status": "accepted",
                "jobID": "remote-job-123",
            },
        ),
    ],
)
def test_ads_plan_resumes_persisted_request_before_newer_ecpds_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    job: dict | None,
) -> None:
    producer = tmp_path / "producer"
    state_path = (
        producer
        / "ads_staging"
        / "cams_ads_2026072000"
        / ".ads_jobs"
        / "2026072000.json"
    )
    payload = {
        "version": 2,
        "dataset": "cams-global-greenhouse-gas-forecasts",
        "server": "https://ads.example.test/api",
        "requestBody": "eyJpbnB1dHMiOnt9fQ==",
        "phase": phase,
        "job": job,
    }
    write_json(state_path, payload)
    monkeypatch.setattr(
        plan_cams_ads_update,
        "validate_cams_contract",
        lambda _root: (_ for _ in ()).throw(
            AssertionError("new ECPDS must not supersede persisted ADS request")
        ),
    )

    result = plan_cams_ads_update.plan_update(producer)

    assert result == (
        "RESUME 2026072000 "
        "2026071800,2026071900,2026072000 2026072000"
    )


def test_ads_plan_fails_closed_for_multiple_persisted_requests(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    for target in ("2026071900", "2026072000"):
        state_path = (
            producer
            / "ads_staging"
            / f"cams_ads_{target}"
            / ".ads_jobs"
            / f"{target}.json"
        )
        write_json(
            state_path,
            {
                "version": 2,
                "dataset": "cams-global-greenhouse-gas-forecasts",
                "server": "https://ads.example.test/api",
                "requestBody": "eyJpbnB1dHMiOnt9fQ==",
                "phase": "submitted",
                "job": {
                    "processID": "cams-global-greenhouse-gas-forecasts",
                    "status": "accepted",
                    "jobID": f"remote-{target}",
                },
            },
        )

    with pytest.raises(ValueError, match="multiple persisted ADS requests"):
        plan_cams_ads_update.plan_update(producer)


def test_ads_validated_run_clears_resume_state_before_next_submission() -> None:
    script = read_script("run_cams_ads_scheduled_cycle.sh")

    assert 'state_file="$work_dir/.ads_jobs/${source_run}.json"' in script
    assert script.count('rm -f -- "$state_file"') == 3
    reuse = script.split(
        'if ! is_truthy "$FORCE_REBUILD_CURRENT"',
        1,
    )[1].split("continue", 1)[0]
    assert 'rm -f -- "$state_file"' in reuse


def test_ads_current_repair_forces_all_sources_and_survives_remote_resume() -> None:
    script = read_script("run_cams_ads_scheduled_cycle.sh")

    assert "WEATHER_CAMS_GREENHOUSE_FORCE_REBUILD_CURRENT" in script
    assert "--force-current" in script
    assert 'force_rebuild_marker="$work_dir/.force_rebuild_current"' in script
    assert '[[ -f "$force_rebuild_marker" ]]' in script
    assert 'rebuild_complete="$work_dir/.full_grid_rebuild_complete/$source_run"' in script
    assert '[[ -f "$rebuild_complete" ]]' in script
    assert '[[ ! -f "$state_file" ]]' in script
    assert ': > "$rebuild_complete"' in script
    assert '"$published_latest" == "$target_run"' in script
    assert '! is_truthy "$FORCE_REBUILD_CURRENT"' in script


def test_ecpds_current_repair_rebuilds_all_retained_runs_in_clean_staging() -> None:
    script = read_script("run_cams_om_production_cycle.sh")

    assert "WEATHER_CAMS_FORCE_REBUILD_CURRENT" in script
    assert "WEATHER_CAMS_COVERAGE_REVISION is required" in script
    assert "refusing CAMS repair for non-current run" in script
    assert '"$STAGING_DIR/cams_global"' in script
    assert '"$STAGING_DIR/data_run/cams_global"' in script
    assert 'REUSED_SOURCE_RUNS=""' in script
    assert script.count('! is_truthy "$FORCE_REBUILD_CURRENT"') >= 2


def test_ads_first_publish_skips_only_a_missing_current_marker() -> None:
    script = read_script("run_cams_ads_scheduled_cycle.sh")

    assert (
        'published_marker="$PRODUCER_ROOT/groups/cams_greenhouse/current/'
        'ready_for_processing.json"'
    ) in script
    assert 'if [[ -e "$published_marker" || -L "$published_marker" ]]; then' in script
    assert 'published_state="$(python3 scripts/validate_native_cams_greenhouse_coverage.py' in script
    assert '<<<"$published_state"' in script
    assert '2>/dev/null' not in script
    assert '|| true' not in script
    assert 'if state="$(python3 scripts/plan_cams_ads_update.py' in script
    assert 'if (( plan_rc != 0 )); then' in script
    assert 'exit "$plan_rc"' in script


def publisher_args(root: Path, source_runs: str, run: str) -> SimpleNamespace:
    staging = root / "staging" / "greenhouse-test"
    staging.mkdir(parents=True)
    return SimpleNamespace(
        output_root=str(root),
        staging_dir=str(staging),
        keep_coverages=1,
        latest_max_forecast_hour=120,
        source_runs=source_runs,
        run=run,
        required_variables="carbon_monoxide",
        coverage_revision="independent-v1",
        left_lon=69.0,
        right_lon=141.0,
        bottom_lat=-1.0,
        top_lat=59.0,
    )


class ReachedRunPayloadValidation(RuntimeError):
    pass


@pytest.mark.parametrize(
    ("source_runs", "run", "error"),
    [
        (
            "2026071900,2026072000",
            "2026072000",
            "must contain three source runs",
        ),
        (
            "2026071700,2026071900,2026072000",
            "2026072000",
            "three consecutive daily cycles",
        ),
        (
            "2026071800,2026071912,2026072000",
            "2026072000",
            "official daily 00 UTC cycle",
        ),
    ],
)
def test_independent_greenhouse_publisher_rejects_non_three_day_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_runs: str,
    run: str,
    error: str,
) -> None:
    monkeypatch.setattr(
        greenhouse_publisher,
        "read_latest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ReachedRunPayloadValidation
        ),
    )

    with pytest.raises(ValueError, match=error):
        greenhouse_publisher.publish_greenhouse_coverage(
            publisher_args(tmp_path / "producer", source_runs, run)
        )


def test_independent_greenhouse_publisher_accepts_three_consecutive_00z_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reached_payload(*_args, **_kwargs):
        raise ReachedRunPayloadValidation

    monkeypatch.setattr(greenhouse_publisher, "read_latest", reached_payload)

    with pytest.raises(ReachedRunPayloadValidation):
        greenhouse_publisher.publish_greenhouse_coverage(
            publisher_args(
                tmp_path / "producer",
                "2026071800,2026071900,2026072000",
                "2026072000",
            )
        )


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def make_greenhouse_contract(
    root: Path,
    source_runs: list[str],
) -> tuple[Path, Path, Path]:
    coverage_id = "cams_greenhouse_native_2026072000_independent-v1"
    coverage = root / "coverages" / "cams_greenhouse" / coverage_id
    runtime = coverage / DOMAIN / "carbon_monoxide" / "chunk.om"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"runtime")
    current = root / "current" / "cams_greenhouse"
    current.mkdir(parents=True)
    latest = source_runs[-1]
    payload = {
        "status": "complete",
        "runtime_format": "openmeteo-native-v1",
        "group": "cams_greenhouse",
        "coverage_id": coverage_id,
        "latest_complete_run": latest,
        "source_runs": source_runs,
        "latest_max_forecast_hour": 120,
        "public_start_utc": "2026-07-18T00:00:00Z",
        "local_day_start_utc": "2026-07-18T00:00:00Z",
        "public_end_utc": "2026-07-25T00:00:00Z",
        "public_hours": 168,
        "domain_grids": {
            DOMAIN: {
                "nx": 721,
                "ny": 601,
                "lat_min": -1.0,
                "lon_min": 69.0,
                "dx": 0.1,
                "dy": 0.1,
                "dt_seconds": 10800,
                "om_file_length": 72,
            }
        },
    }
    files, bytes_total = prepare_cams_ads_staging.coverage_data_stats(coverage)
    payload["files"] = files
    payload["bytes"] = bytes_total
    write_json(coverage / "coverage.json", payload)
    marker = dict(payload)
    marker["coverage_path"] = f"coverages/cams_greenhouse/{coverage_id}"
    marker["products"] = {
        DOMAIN: {
            "coverage_id": coverage_id,
            "runtime_domain": DOMAIN,
            "grid": payload["domain_grids"][DOMAIN],
        }
    }
    marker_path = (
        root
        / "groups"
        / "cams_greenhouse"
        / "current"
        / "ready_for_processing.json"
    )
    write_json(marker_path, marker)
    return coverage, current, marker_path


def patch_greenhouse_pointer_and_payload_validation(
    monkeypatch: pytest.MonkeyPatch,
    current: Path,
    coverage: Path,
) -> None:
    original_is_symlink = Path.is_symlink
    original_resolve = Path.resolve

    def fake_is_symlink(path: Path) -> bool:
        if path == current:
            return True
        return original_is_symlink(path)

    def fake_resolve(path: Path, strict: bool = False) -> Path:
        if path == current:
            return original_resolve(coverage, strict=True)
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(Path, "resolve", fake_resolve)
    monkeypatch.setattr(
        greenhouse_validator,
        "read_latest",
        lambda *_args, **_kwargs: {"status": "complete"},
    )
    monkeypatch.setattr(
        greenhouse_validator,
        "validate_run_metadata",
        lambda *_args, **_kwargs: {"variables": ["carbon_monoxide"]},
    )


def test_independent_greenhouse_validator_accepts_exact_three_day_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "producer"
    source_runs = ["2026071800", "2026071900", "2026072000"]
    coverage, current, _ = make_greenhouse_contract(root, source_runs)
    patch_greenhouse_pointer_and_payload_validation(monkeypatch, current, coverage)

    contract = greenhouse_validator.validate_greenhouse_contract(root)

    assert contract["source_runs"] == source_runs
    assert contract["latest_complete_run"] == "2026072000"


@pytest.mark.parametrize(
    ("source_runs", "error"),
    [
        (["2026071900", "2026072000"], "exactly three source runs"),
        (
            ["2026071700", "2026071900", "2026072000"],
            "consecutive daily cycles",
        ),
        (["2026071800", "2026071912", "2026072000"], "00 UTC cycles"),
    ],
)
def test_independent_greenhouse_validator_rejects_invalid_three_day_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_runs: list[str],
    error: str,
) -> None:
    root = tmp_path / "producer"
    coverage, current, _ = make_greenhouse_contract(root, source_runs)
    patch_greenhouse_pointer_and_payload_validation(monkeypatch, current, coverage)

    with pytest.raises(ValueError, match=error):
        greenhouse_validator.validate_greenhouse_contract(root)


def test_api_identity_and_snapshot_load_independent_greenhouse_namespace() -> None:
    api = (ROOT / "om_api" / "src" / "api.rs").read_text(encoding="utf-8")
    snapshot = (ROOT / "om_api" / "src" / "snapshot.rs").read_text(
        encoding="utf-8"
    )
    native = (ROOT / "om_api" / "src" / "native.rs").read_text(
        encoding="utf-8"
    )

    assert "cams_greenhouse_ready: Option<GroupIdentity>" in api
    assert 'cams_greenhouse_ready: marker(data_root, "cams_greenhouse")?' in api
    assert 'join("groups/cams_greenhouse/current/ready_for_processing.json")' in snapshot
    remove_old = 'products.remove("cams_global_greenhouse_gases")'
    load_independent = '"cams_greenhouse",\n                CAMS_GREENHOUSE_PRODUCTS'
    assert remove_old in snapshot
    assert load_independent in snapshot
    assert snapshot.index(remove_old) < snapshot.index(load_independent)
    assert '"cams_greenhouse" => (3, 24)' in native
    assert 'group == "cams_greenhouse" && parsed.iter().any' in native
