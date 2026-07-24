from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ecmwf_contract
import build_webp
import probe_ecmwf_open_data_run as probe
import publish_ecmwf_release as publisher


def test_pinned_ecmwf_contract_is_complete_and_deterministic() -> None:
    assert (
        ecmwf_contract.OPENMETEO_UPSTREAM_COMMIT
        == "b743cbc9a7fab3f8f7dda85968fb770eee48b9ec"
    )
    assert ecmwf_contract.MODEL == "ecmwf_ifs025"
    assert ecmwf_contract.PUBLIC_BOUNDS == (70.0, 140.0, 0.0, 58.0)
    assert ecmwf_contract.STORAGE_BOUNDS == (68.0, 142.0, -2.0, 60.0)
    assert len(ecmwf_contract.SURFACE_RAW_VARIABLES) == 32
    assert len(ecmwf_contract.PRESSURE_RAW_VARIABLES) == 84
    assert len(ecmwf_contract.RAW_VARIABLES) == 116
    assert len(set(ecmwf_contract.RAW_VARIABLES)) == 116
    assert len(ecmwf_contract.ROLLING_FALLBACK_VARIABLES) == 6


def test_ecmwf_source_plan_seeds_predecessors_oldest_to_newest() -> None:
    plan = ecmwf_contract.source_run_plan("2026072300")

    assert len(plan) == 13
    assert plan[0] == ("2026072000", 360)
    assert plan[1] == ("2026072006", 144)
    assert plan[-2] == ("2026072218", 144)
    assert plan[-1] == ("2026072300", 360)
    assert [run for run, _ in plan] == sorted(run for run, _ in plan)


@pytest.mark.parametrize("run", ("2026072301", "2026-07-23", "bad"))
def test_ecmwf_contract_rejects_invalid_runs(run: str) -> None:
    with pytest.raises(ValueError):
        ecmwf_contract.parse_run(run)


def complete_index(run: str) -> list[dict[str, object]]:
    common: dict[str, object] = {
        "date": run[:8],
        "time": f"{run[8:]}00",
        "step": "360",
    }
    records = [
        {**common, "levtype": "sfc", "param": param}
        for param in sorted(ecmwf_contract.SURFACE_PROBE_PARAMS)
    ]
    records.extend(
        {
            **common,
            "levtype": "sol",
            "param": param,
            "levelist": str(level),
        }
        for param, level in sorted(ecmwf_contract.SOIL_PROBE_FIELDS)
    )
    records.extend(
        {
            **common,
            "levtype": "pl",
            "param": param,
            "levelist": str(level),
        }
        for param in sorted(ecmwf_contract.PRESSURE_PROBE_PARAMS)
        for level in ecmwf_contract.PRESSURE_LEVELS_HPA
    )
    return records


def test_final_index_probe_validates_full_surface_and_pressure_inventory() -> None:
    run = "2026072300"
    result = probe.validate(run, complete_index(run))

    assert result == {
        "status": "complete",
        "run": run,
        "max_forecast_hour": 360,
        "index_records": 115,
        "required_surface_params": 23,
        "required_soil_fields": 8,
        "required_pressure_fields": 84,
    }


def test_final_index_probe_stops_on_first_missing_pressure_field() -> None:
    records = complete_index("2026072300")
    records = [
        record
        for record in records
        if not (
            record["levtype"] == "pl"
            and record["param"] == "w"
            and record["levelist"] == "10"
        )
    ]

    with pytest.raises(ValueError, match="inventory is incomplete"):
        probe.validate("2026072300", records)


def test_final_index_probe_stops_on_first_missing_soil_field() -> None:
    records = complete_index("2026072300")
    records = [
        record
        for record in records
        if not (
            record["levtype"] == "sol"
            and record["param"] == "vsw"
            and record["levelist"] == "4"
        )
    ]

    with pytest.raises(ValueError, match="inventory is incomplete"):
        probe.validate("2026072300", records)


def create_complete_staging(root: Path, run: str) -> Path:
    staging = root / "staging" / f"ecmwf_{run}"
    model = staging / ecmwf_contract.MODEL
    static = model / "static"
    static.mkdir(parents=True)
    epoch = int(
        datetime.strptime(run, "%Y%m%d%H")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )
    (static / "meta.json").write_text(
        json.dumps(
            {
                "last_run_initialisation_time": epoch,
                "temporal_resolution_seconds": 10800,
                "data_end_time": epoch + 360 * 3600,
            }
        ),
        encoding="utf-8",
    )
    (static / "HSURF.om").write_bytes(b"elevation")
    for variable in ecmwf_contract.RAW_VARIABLES:
        variable_root = model / variable
        variable_root.mkdir()
        (variable_root / "chunk.om").write_bytes(b"om")
    (staging / "production-progress.json").write_text(
        json.dumps(
            {
                "target_run": run,
                "completed_runs": [
                    source_run
                    for source_run, _ in ecmwf_contract.source_run_plan(run)
                ],
            }
        ),
        encoding="utf-8",
    )
    return staging


def test_release_publisher_requires_full_inventory_and_publishes_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ecmwf"
    staging = create_complete_staging(root, "2026072300")

    def portable_symlink(target: Path, link: Path) -> None:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_text(str(target), encoding="utf-8")

    monkeypatch.setattr(publisher, "atomic_symlink", portable_symlink)
    marker = publisher.publish(
        root=root,
        staging=staging,
        run="2026072300",
        image="weather-forecast-ecmwf:ifs025-test",
        patch_sha256="a" * 64,
        source_revision="b" * 40,
    )

    release = root / "releases" / "ecmwf_ifs025_2026072300"
    assert not staging.exists()
    assert release.is_dir()
    assert marker["status"] == "complete"
    assert marker["latest_max_forecast_hour"] == 360
    assert marker["hourly_frames"] == 361
    assert marker["daily_frames"] == 15
    assert marker["required_variables"] == list(ecmwf_contract.RAW_VARIABLES)
    assert marker["missing_required_variables"] == []
    assert marker["missing_optional_variables"] == []
    assert marker["grid"] == {
        "grid_type": "regional_regular_lat_lon",
        "full_nx": 1440,
        "full_ny": 721,
        "x0": 992,
        "y0": 352,
        "nx": 297,
        "ny": 249,
        "dx": 0.25,
        "dy": 0.25,
        "lon_min": 68.0,
        "lat_min": -2.0,
        "requested_bounds": {
            "left_lon": 68.0,
            "right_lon": 142.0,
            "bottom_lat": -2.0,
            "top_lat": 60.0,
        },
    }
    group_marker = json.loads(
        (
            root
            / "groups"
            / "ecmwf"
            / "current"
            / "ready_for_processing.json"
        ).read_text(encoding="utf-8")
    )
    assert group_marker == marker


def test_release_validation_rejects_transient_duplicate_data(tmp_path: Path) -> None:
    root = tmp_path / "ecmwf"
    staging = create_complete_staging(root, "2026072300")
    (staging / "data_run").mkdir()

    with pytest.raises(ValueError, match="transient or duplicate"):
        publisher.validate_release(staging, "2026072300")


def test_regional_patch_applies_to_exact_locked_upstream() -> None:
    upstream = ROOT / "vendor" / "open-meteo-ecmwf"
    patch = ROOT / "vendor" / "patches" / "open-meteo-ecmwf-regional.patch"
    revision = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    assert revision == ecmwf_contract.OPENMETEO_UPSTREAM_COMMIT
    completed = subprocess.run(
        ["git", "-C", str(upstream), "apply", "--check", str(patch)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    numstat = subprocess.check_output(
        ["git", "-C", str(upstream), "apply", "--numstat", str(patch)],
        text=True,
    )
    assert (
        "252\t0\tSources/App/Ecmwf/EcmwfRegionalGrid.swift" in numstat
    )
    source = patch.read_text(encoding="utf-8")
    assert "WEATHER_ECMWF_REGIONAL_GRID" in source
    assert "cropToRuntimeGrid" in source
    assert "estimatedNumberOfGridCells" in source
    assert '@Flag(name: "skip-full-run")' in source
    assert (
        '             let shortName = message.get(attribute: "shortName")!\n'
        "+            var grib2d = GribArray2D("
        "nx: domain.sourceGrid.nx, ny: domain.sourceGrid.ny)\n"
        "             try grib2d.load(message: message)"
    ) in source


def test_ecmwf_pipeline_uses_panel_state_without_batch_lock() -> None:
    cycle = (SCRIPTS / "run_ecmwf_om_production_cycle.sh").read_text(
        encoding="utf-8"
    )
    probe_cycle = (SCRIPTS / "run_ecmwf_probe_and_cycle.sh").read_text(
        encoding="utf-8"
    )
    installer = (SCRIPTS / "install_openmeteo_cron.sh").read_text(
        encoding="utf-8"
    )

    for source in (cycle, probe_cycle):
        assert "flock" not in source
        assert "LOCK_FILE" not in source
        assert "/tmp/weather_openmeteo_production.lock" not in source
        assert "WEATHER_1PANEL_VERIFIED_TASK" in source
    assert installer.count('"weather_ecmwf_probe_cycle",') == 1
    assert "scripts/run_ecmwf_probe_and_cycle.sh" in installer
    assert "VALUES (CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, 'shell', ?, ?, ?, 'Disable')" in installer


def test_ecmwf_api_mounts_model_and_dem_below_writable_data_tmpfs() -> None:
    installer = (SCRIPTS / "install_ecmwf_api_service.sh").read_text(
        encoding="utf-8"
    )

    data_tmpfs = "--tmpfs /app/data:rw,noexec,nosuid,size=1m"
    model_mount = (
        "--volume $ECMWF_ROOT/current/ecmwf_ifs025:"
        "/app/data/ecmwf_ifs025:ro"
    )
    dem_mount = "--volume $DEM_ROOT:/app/data/copernicus_dem90:ro"
    assert data_tmpfs in installer
    assert model_mount in installer
    assert dem_mount in installer
    assert "$ECMWF_ROOT/current:/app/data:ro" not in installer
    assert installer.index(data_tmpfs) < installer.index(model_mount)
    assert installer.index(model_mount) < installer.index(dem_mount)
    assert 'RELEASE_MARKER="$ECMWF_ROOT/groups/ecmwf/current/ready_for_processing.json"' in installer
    assert 'printf \'%s\\n\' "$SOURCE_REVISION" >"$INSTALL_ROOT/source-revision"' in installer
    assert (
        'printf \'%s\\n\' "$DATA_SOURCE_REVISION" '
        '>"$INSTALL_ROOT/data-source-revision"'
    ) in installer
    assert "--source-revision $DATA_SOURCE_REVISION" in installer
    assert "--source-revision $SOURCE_REVISION" not in installer


def test_ecmwf_image_is_isolated_and_records_exact_provenance() -> None:
    dockerfile = (
        ROOT / "docker" / "openmeteo-ecmwf.Dockerfile"
    ).read_text(encoding="utf-8")
    build = (SCRIPTS / "build_openmeteo_ecmwf_image.sh").read_text(
        encoding="utf-8"
    )

    assert "COPY vendor/open-meteo-ecmwf/" in dockerfile
    assert "vendor/open-meteo/" not in dockerfile
    assert "docker-container-build:latest" not in dockerfile
    assert "docker-container-run:latest" not in dockerfile
    assert "sha256:e0ef0354d44c4a9330eabe68be5b29cf303ca654444db4ae76f2b601ec161e6f" in dockerfile
    assert "sha256:7e6ee634cc774abdcf1875dc632229d51368a2b32e4714fed880c41bd7155aff" in dockerfile
    assert "git apply --check /build/open-meteo-regional.patch" in dockerfile
    assert "test ! -d .git" in dockerfile
    assert "rm -f -- .git" in dockerfile
    assert "io.weather-forecast.component=ecmwf-native-engine" in dockerfile
    assert "io.weather-forecast.openmeteo-upstream-commit" in dockerfile
    assert "io.weather-forecast.ecmwf-patch-sha256" in dockerfile
    assert ecmwf_contract.OPENMETEO_UPSTREAM_COMMIT in build
    assert "git -C \"$UPSTREAM_DIR\" apply --check" in build


def test_ecmwf_webp_uses_native_quarter_degree_grid_and_16_layers() -> None:
    grid = build_webp.compute_ecmwf025_region_grid(
        left_lon=70.0,
        right_lon=140.0,
        bottom_lat=0.0,
        top_lat=58.0,
    )
    layers = build_webp.layer_definitions_for_scope("ecmwf")

    assert (grid.width, grid.height) == (281, 233)
    assert grid.longitude_values[0] == 70.0
    assert grid.longitude_values[-1] == 140.0
    assert grid.latitude_values[0] == 58.0
    assert grid.latitude_values[-1] == 0.0
    assert grid.row_order == "north_to_south"
    assert len(layers) == 16
    assert "vis" not in {layer.name for layer in layers}
    assert "uv_index" not in {layer.name for layer in layers}


def test_ecmwf_webp_api_and_catalog_contract() -> None:
    params = build_webp.build_layer_api_params(
        scope="ecmwf",
        latitudes=[31.25],
        longitudes=[121.5],
        variables=["temperature_2m"],
        model=None,
        domain=None,
        start_hour="2026-07-23T00:00",
        end_hour="2026-07-23T01:00",
        run="2026-07-23T00:00",
        request_forecast_hours=2,
    )
    catalog = build_webp.layer_catalog_payload()
    installed_catalog = json.loads(
        (ROOT / "config" / "weather_layer_catalog.json").read_text(
            encoding="utf-8"
        )
    )

    assert params["models"] == "ecmwf_ifs025"
    assert params["wind_speed_unit"] == "ms"
    assert params["run"] == "2026-07-23T00:00"
    assert params["forecast_hours"] == "2"
    assert catalog == installed_catalog
    assert catalog["products"]["ecmwf"]["manifest"] == (
        "ecmwf_ifs025_data.json"
    )
    assert len(catalog["products"]["ecmwf"]["layers"]) == 16


def test_ecmwf_webp_publisher_has_no_test_batch_lock() -> None:
    script = (SCRIPTS / "build_openmeteo_ecmwf_layers.sh").read_text(
        encoding="utf-8"
    )

    assert "flock" not in script
    assert "LOCK_FILE" not in script
    assert "/tmp/" not in script
    assert "--scope ecmwf" in script
    assert 'FRAME_COUNT="${WEATHER_OPENMETEO_ECMWF_LAYER_FRAME_COUNT:-121}"' in script
    assert '"layer_count": 16' in script


def test_ecmwf_proxy_route_is_managed_and_has_no_test_lock() -> None:
    script = (SCRIPTS / "install_ecmwf_proxy_route.sh").read_text(
        encoding="utf-8"
    )

    assert "BEGIN weather-forecast ECMWF API (managed)" in script
    assert "location ^~ /v1/ecmwf" in script
    assert "proxy_pass http://127.0.0.1:{port}" in script
    assert "proxy_set_header Host api.open-meteo.com;" in script
    assert "openresty -t" in script
    assert "openresty -s reload" in script
    assert "flock" not in script
    assert "LOCK_FILE" not in script
