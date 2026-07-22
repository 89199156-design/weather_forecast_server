import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = {
    "om-api",
    "om-raw-point",
    "om-webp",
    "om-grid-verify",
    "om-webp-api-verify",
    "om-webp-inspect",
}

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux deployment scripts")


def run(command: list[str], *, cwd: Path, env: dict[str, str], check: bool = True):
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def write_executable(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def prepare_repository(tmp_path: Path) -> tuple[Path, Path]:
    app = tmp_path / "app"
    (app / "scripts").mkdir(parents=True)
    (app / "om_api").mkdir()
    (app / "om_webp").mkdir()
    shutil.copy2(ROOT / "scripts/build_native_rust_artifacts.sh", app / "scripts")
    shutil.copy2(ROOT / "scripts/deploy_native_rust_artifacts.sh", app / "scripts")
    shutil.copy2(ROOT / "scripts/check_1panel_v1_task_state.py", app / "scripts")
    shutil.copy2(ROOT / "scripts/install_native_api_dem_root.sh", app / "scripts")
    shutil.copy2(ROOT / "rust-toolchain.toml", app)
    (app / "om_api/Cargo.toml").write_text("[package]\nname='om-api'\nversion='0.1.0'\n", encoding="utf-8")
    (app / "om_webp/Cargo.toml").write_text("[package]\nname='om-webp'\nversion='0.1.0'\n", encoding="utf-8")

    git(app, "init", "-b", "main")
    git(app, "config", "user.name", "Rust Deployment Test")
    git(app, "config", "user.email", "rust-deploy-test@example.invalid")
    git(app, "add", ".")
    git(app, "commit", "-m", "test source")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    git(app, "remote", "add", "origin", str(origin))
    git(app, "push", "-u", "origin", "main")
    return app, origin


def prepare_fake_tools(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    pid_file = tmp_path / "api.pid"
    write_executable(
        fake_bin / "cargo",
        r"""
        #!/usr/bin/env bash
        set -euo pipefail
        if [[ "${1:-}" == "--version" ]]; then
          echo "cargo 1.97.1 (test)"
          exit 0
        fi
        printf '%s\n' "$PWD" >> "$TEST_CARGO_PWD_LOG"
        mkdir -p "$CARGO_TARGET_DIR/release"
        for artifact in om-api om-raw-point om-webp om-grid-verify om-webp-api-verify om-webp-inspect; do
          install -m 0755 /bin/sleep "$CARGO_TARGET_DIR/release/$artifact"
        done
        """,
    )
    write_executable(
        fake_bin / "rustc",
        r"""
        #!/usr/bin/env bash
        if [[ "${1:-}" == "-Vv" ]]; then
          printf '%s\n' \
            'rustc 1.97.1 (test)' \
            'binary: rustc' \
            'commit-hash: 0000000000000000000000000000000000000000' \
            'host: x86_64-unknown-linux-gnu' \
            'release: 1.97.1'
        else
          echo 'rustc 1.97.1 (test)'
        fi
        """,
    )
    write_executable(
        fake_bin / "sudo",
        r"""
        #!/usr/bin/env bash
        set -euo pipefail
        [[ "${1:-}" == "-n" ]]
        shift
        exec "$@"
        """,
    )
    write_executable(
        fake_bin / "systemctl",
        r"""
        #!/usr/bin/env bash
        set -euo pipefail
        command_name="${1:?missing systemctl command}"
        shift
        case "$command_name" in
          restart)
            if [[ -s "$TEST_PID_FILE" ]]; then
              old_pid="$(cat "$TEST_PID_FILE")"
              kill "$old_pid" 2>/dev/null || true
            fi
            dem_root=""
            if [[ -f "${TEST_SYSTEMD_DROPIN_PATH:-}" ]]; then
              dem_root="$(sed -n 's/^Environment="OM_DEM_ROOT=\(.*\)"$/\1/p' \
                "$TEST_SYSTEMD_DROPIN_PATH")"
            fi
            if [[ -n "$dem_root" ]]; then
              OM_DEM_ROOT="$dem_root" \
                nohup "$WEATHER_OM_API_ROOT/bin/om-api" 300 >/dev/null 2>&1 \
                  9>&- 8>&- 7>&- 6>&- 5>&- &
            else
              nohup "$WEATHER_OM_API_ROOT/bin/om-api" 300 >/dev/null 2>&1 \
                9>&- 8>&- 7>&- 6>&- 5>&- &
            fi
            echo "$!" > "$TEST_PID_FILE"
            ;;
          daemon-reload)
            ;;
          is-active)
            pid="$(cat "$TEST_PID_FILE")"
            kill -0 "$pid"
            ;;
          show)
            cat "$TEST_PID_FILE"
            ;;
          *)
            echo "unexpected systemctl command: $command_name" >&2
            exit 2
            ;;
        esac
        """,
    )
    write_executable(
        fake_bin / "curl",
        r"""
        #!/usr/bin/env bash
        if [[ "${TEST_CURL_FAIL:-0}" == "1" ]]; then
          exit 22
        fi
        exit 0
        """,
    )
    return fake_bin, pid_file


def deployment_env(tmp_path: Path, origin: Path, fake_bin: Path, pid_file: Path) -> dict[str, str]:
    dem_root = tmp_path / "runtime-data/point"
    dem_static = dem_root / "copernicus_dem90/static"
    dem_static.mkdir(parents=True)
    (dem_static / "lat_0.om").write_bytes(b"test-dem")
    dropin = tmp_path / "systemd/weather-om-api.service.d/20-dem-root.conf"
    panel_db = tmp_path / "1Panel.db"
    with sqlite3.connect(panel_db) as connection:
        connection.execute(
            "create table cronjobs ("
            "id integer primary key, name text, status text, retain_copies integer)"
        )
        connection.execute(
            "create table job_records (id integer primary key, cronjob_id integer, "
            "status text, start_time text, records text)"
        )
        for task_id, task_name in enumerate(
            (
                "weather_gfs_probe_cycle",
                "weather_cams_ecpds_probe_cycle",
                "weather_cams_ads_cycle",
            ),
            start=1,
        ):
            connection.execute(
                "insert into cronjobs (id, name, status, retain_copies) "
                "values (?, ?, 'Enable', 100)",
                (task_id, task_name),
            )
        connection.commit()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "TEST_CARGO_PWD_LOG": str(tmp_path / "cargo-pwd.log"),
            "TEST_PID_FILE": str(pid_file),
            "TEST_SYSTEMD_DROPIN_PATH": str(dropin),
            "WEATHER_RUST_EXPECTED_REMOTE_URL": str(origin),
            "WEATHER_RUST_TARGET_DIR": str(tmp_path / "target"),
            "WEATHER_RUST_BUILD_ROOT": str(tmp_path / "build"),
            "WEATHER_RUST_BUILD_LOCK_FILE": str(tmp_path / "build.lock"),
            "WEATHER_RUST_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
            "WEATHER_1PANEL_DB": str(panel_db),
            "WEATHER_OM_API_ROOT": str(tmp_path / "api-root"),
            "WEATHER_OM_WEBP_ROOT": str(tmp_path / "webp-root"),
            "WEATHER_OM_DEM_ROOT": str(dem_root),
            "WEATHER_DEM_REQUIRED_LAT_MIN": "0",
            "WEATHER_DEM_REQUIRED_LAT_MAX": "0",
            "WEATHER_OM_API_DEM_DROPIN_PATH": str(dropin),
            "WEATHER_OM_API_CONFIG_LOCK_FILE": str(tmp_path / "api-config.lock"),
            "WEATHER_SUDO_BIN": str(fake_bin / "sudo"),
            "WEATHER_SYSTEMCTL_BIN": str(fake_bin / "systemctl"),
            "WEATHER_CURL_BIN": str(fake_bin / "curl"),
        }
    )
    return env


def install_old_links(env: dict[str, str]) -> dict[Path, str]:
    api_root = Path(env["WEATHER_OM_API_ROOT"])
    webp_root = Path(env["WEATHER_OM_WEBP_ROOT"])
    old_targets: dict[Path, str] = {}
    for root, artifacts in (
        (api_root, ("om-api", "om-raw-point")),
        (webp_root, ("om-webp", "om-grid-verify", "om-webp-api-verify", "om-webp-inspect")),
    ):
        release = root / "releases/old"
        binary_root = root / "bin"
        release.mkdir(parents=True)
        binary_root.mkdir(parents=True)
        for artifact in artifacts:
            target = release / artifact
            shutil.copy2("/bin/sleep", target)
            target.chmod(0o755)
            link = binary_root / artifact
            link.symlink_to(target)
            old_targets[link] = str(target)
    return old_targets


def stop_fake_service(pid_file: Path) -> None:
    if not pid_file.exists():
        return
    try:
        os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGTERM)
    except (ProcessLookupError, ValueError):
        pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_build_rejects_untracked_source(tmp_path: Path):
    app, origin = prepare_repository(tmp_path)
    fake_bin, pid_file = prepare_fake_tools(tmp_path)
    env = deployment_env(tmp_path, origin, fake_bin, pid_file)
    (app / "untracked.rs").write_text("not committed\n", encoding="utf-8")

    completed = run(
        ["bash", "scripts/build_native_rust_artifacts.sh", str(tmp_path / "build")],
        cwd=app,
        env=env,
        check=False,
    )

    assert completed.returncode != 0
    assert "dirty worktree" in completed.stderr
    assert not Path(env["TEST_CARGO_PWD_LOG"]).exists()


def test_build_records_all_artifacts_from_exact_remote_main(tmp_path: Path):
    app, origin = prepare_repository(tmp_path)
    fake_bin, pid_file = prepare_fake_tools(tmp_path)
    env = deployment_env(tmp_path, origin, fake_bin, pid_file)
    output = Path(env["WEATHER_RUST_BUILD_ROOT"])

    run(
        ["bash", str(app / "scripts/build_native_rust_artifacts.sh"), str(output)],
        cwd=tmp_path,
        env=env,
    )

    manifest = json.loads((output / "build.json").read_text(encoding="utf-8"))
    assert set(manifest["artifacts"]) == ARTIFACTS
    assert manifest["source_branch"] == "main"
    assert manifest["remote_url"] == str(origin)
    assert manifest["revision"] == git(app, "rev-parse", "origin/main")
    assert "release: 1.97.1" in manifest["rustc"]
    for artifact in ARTIFACTS:
        assert manifest["artifacts"][artifact]["sha256"] == sha256(output / artifact)
    assert set(Path(env["TEST_CARGO_PWD_LOG"]).read_text(encoding="utf-8").splitlines()) == {str(app)}


def test_deploy_uses_one_immutable_release_and_verifies_running_api(tmp_path: Path):
    app, origin = prepare_repository(tmp_path)
    fake_bin, pid_file = prepare_fake_tools(tmp_path)
    env = deployment_env(tmp_path, origin, fake_bin, pid_file)
    install_old_links(env)
    try:
        first = run(
            ["bash", str(app / "scripts/deploy_native_rust_artifacts.sh")],
            cwd=tmp_path,
            env=env,
        )
        release_id = next(
            line.split("=", 1)[1]
            for line in first.stdout.splitlines()
            if line.startswith("release_id=")
        )
        api_root = Path(env["WEATHER_OM_API_ROOT"])
        webp_root = Path(env["WEATHER_OM_WEBP_ROOT"])
        api_release = api_root / "releases" / release_id
        webp_release = webp_root / "releases" / release_id
        assert {path.name for path in api_release.iterdir()} == {"build.json", "om-api", "om-raw-point"}
        assert {path.name for path in webp_release.iterdir()} == {
            "build.json",
            "om-webp",
            "om-grid-verify",
            "om-webp-api-verify",
            "om-webp-inspect",
        }
        assert (api_root / "bin/om-api").resolve() == api_release / "om-api"
        assert (api_root / "bin/om-raw-point").resolve() == api_release / "om-raw-point"
        for artifact in ARTIFACTS - {"om-api", "om-raw-point"}:
            assert (webp_root / "bin" / artifact).resolve() == webp_release / artifact
        dropin = Path(env["WEATHER_OM_API_DEM_DROPIN_PATH"])
        assert dropin.read_text(encoding="utf-8") == (
            "[Service]\n"
            f'Environment="OM_DEM_ROOT={env["WEATHER_OM_DEM_ROOT"]}"\n'
            f'ReadOnlyPaths={env["WEATHER_OM_DEM_ROOT"]}/copernicus_dem90\n'
        )
        running_environment = Path(
            f"/proc/{pid_file.read_text(encoding='utf-8').strip()}/environ"
        ).read_bytes().split(b"\0")
        assert f'OM_DEM_ROOT={env["WEATHER_OM_DEM_ROOT"]}'.encode() in running_environment

        original_stat = (api_release / "om-api").stat()
        repeated = run(
            ["bash", str(app / "scripts/deploy_native_rust_artifacts.sh")],
            cwd=tmp_path,
            env=env,
            check=False,
        )
        assert repeated.returncode == 0, repeated.stderr
        repeated_stat = (api_release / "om-api").stat()
        assert (repeated_stat.st_ino, repeated_stat.st_mtime_ns) == (
            original_stat.st_ino,
            original_stat.st_mtime_ns,
        )

        (api_release / "om-raw-point").write_bytes(b"corrupt immutable release")
        failed = run(
            ["bash", str(app / "scripts/deploy_native_rust_artifacts.sh")],
            cwd=tmp_path,
            env=env,
            check=False,
        )
        assert failed.returncode != 0
        assert "checksum mismatch" in failed.stderr
        assert (api_release / "om-raw-point").read_bytes() == b"corrupt immutable release"
    finally:
        stop_fake_service(pid_file)


def test_deploy_has_no_weather_task_file_lock_gate():
    script = (ROOT / "scripts/deploy_native_rust_artifacts.sh").read_text(
        encoding="utf-8"
    )
    assert "WEATHER_OPENMETEO_GFS_PROBE_LOCK_FILE" not in script
    assert "WEATHER_OPENMETEO_GFS_LOCK_FILE" not in script
    assert "WEATHER_OPENMETEO_CAMS_FTP_SCHEDULE_LOCK_FILE" not in script
    assert "WEATHER_OPENMETEO_CAMS_FTP_LOCK_FILE" not in script
    assert "WEATHER_OPENMETEO_CAMS_ADS_SCHEDULE_LOCK_FILE" not in script
    assert "--require-production-idle" in script


def test_deploy_rolls_back_every_link_when_health_check_fails(tmp_path: Path):
    app, origin = prepare_repository(tmp_path)
    fake_bin, pid_file = prepare_fake_tools(tmp_path)
    env = deployment_env(tmp_path, origin, fake_bin, pid_file)
    old_targets = install_old_links(env)
    env["TEST_CURL_FAIL"] = "1"
    try:
        completed = run(
            ["bash", str(app / "scripts/deploy_native_rust_artifacts.sh")],
            cwd=tmp_path,
            env=env,
            check=False,
        )

        assert completed.returncode != 0
        assert "restoring previous Rust executable links" in completed.stderr
        assert not Path(env["WEATHER_OM_API_DEM_DROPIN_PATH"]).exists()
        for link, old_target in old_targets.items():
            assert link.is_symlink()
            assert os.readlink(link) == old_target
        running_executable = Path(f"/proc/{pid_file.read_text(encoding='utf-8').strip()}/exe").resolve()
        assert running_executable == Path(old_targets[Path(env["WEATHER_OM_API_ROOT"]) / "bin/om-api"])
    finally:
        stop_fake_service(pid_file)


def test_deploy_restores_previous_dem_dropin_when_install_health_check_fails(
    tmp_path: Path,
):
    app, origin = prepare_repository(tmp_path)
    fake_bin, pid_file = prepare_fake_tools(tmp_path)
    env = deployment_env(tmp_path, origin, fake_bin, pid_file)
    old_targets = install_old_links(env)
    dropin = Path(env["WEATHER_OM_API_DEM_DROPIN_PATH"])
    dropin.parent.mkdir(parents=True)
    previous = (
        "[Service]\n"
        'Environment="OM_DEM_ROOT=/srv/previous-point"\n'
        "ReadOnlyPaths=/srv/previous-point/copernicus_dem90\n"
    )
    dropin.write_text(previous, encoding="utf-8")
    env["TEST_CURL_FAIL"] = "1"
    try:
        completed = run(
            ["bash", str(app / "scripts/deploy_native_rust_artifacts.sh")],
            cwd=tmp_path,
            env=env,
            check=False,
        )

        assert completed.returncode != 0
        assert "restoring the previous systemd configuration" in completed.stderr
        assert dropin.read_text(encoding="utf-8") == previous
        for link, old_target in old_targets.items():
            assert os.readlink(link) == old_target
    finally:
        stop_fake_service(pid_file)


@pytest.mark.parametrize(
    "task_id,task_name",
    (
        (1, "weather_gfs_probe_cycle"),
        (2, "weather_cams_ecpds_probe_cycle"),
        (3, "weather_cams_ads_cycle"),
    ),
)
def test_deploy_refuses_to_overlap_any_production_task(
    tmp_path: Path, task_id: int, task_name: str
):
    app, origin = prepare_repository(tmp_path)
    fake_bin, pid_file = prepare_fake_tools(tmp_path)
    env = deployment_env(tmp_path, origin, fake_bin, pid_file)
    old_targets = install_old_links(env)
    log_path = tmp_path / f"{task_name}.log"
    log_path.touch()
    with sqlite3.connect(env["WEATHER_1PANEL_DB"]) as connection:
        connection.execute(
            "insert into job_records "
            "(id, cronjob_id, status, start_time, records) "
            "values (?, ?, 'Waiting', '2026-07-23 00:00:00', ?)",
            (100 + task_id, task_id, str(log_path)),
        )
        connection.commit()
    completed = run(
        ["bash", str(app / "scripts/deploy_native_rust_artifacts.sh")],
        cwd=tmp_path,
        env=env,
        check=False,
    )

    assert completed.returncode != 0
    assert task_name in completed.stderr
    for link, old_target in old_targets.items():
        assert os.readlink(link) == old_target
    assert not pid_file.exists()


def test_scripts_are_valid_bash_and_toolchain_is_pinned():
    subprocess.run(
        [
            "bash",
            "-n",
            str(ROOT / "scripts/build_native_rust_artifacts.sh"),
            str(ROOT / "scripts/deploy_native_rust_artifacts.sh"),
            str(ROOT / "scripts/install_native_api_dem_root.sh"),
        ],
        check=True,
    )
    toolchain = (ROOT / "rust-toolchain.toml").read_text(encoding="utf-8")
    assert 'channel = "1.97.1"' in toolchain
    assert 'channel = "stable"' not in toolchain
