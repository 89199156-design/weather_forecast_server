import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "run_openmeteo_target_validation.py"
    spec = importlib.util.spec_from_file_location("run_openmeteo_target_validation", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_point_batches_cover_1000_unique_points_in_100_groups():
    runner = load_module()

    batches = runner.build_point_batches(
        batches=100,
        points_per_batch=10,
        left_lon=70.0,
        right_lon=140.0,
        bottom_lat=0.0,
        top_lat=58.0,
        point_offset=0.0,
    )

    assert len(batches) == 100
    assert {len(batch) for batch in batches} == {10}
    points = [point for batch in batches for point in batch]
    assert len(points) == 1000
    assert len({runner.point_key(point) for point in points}) == 1000


def test_target_variables_match_client_weather_scope_without_soil_outputs():
    runner = load_module()

    gfs = runner.target_variables_for_scope("gfs")
    cams = runner.target_variables_for_scope("cams")

    assert "visibility" in gfs
    assert "weather_code" in gfs
    assert "temperature_2m" in gfs
    assert "precipitation" in gfs
    assert "rain" in gfs
    assert "snowfall" in gfs
    assert "cape" in gfs
    assert "wind_u_component_10m" in gfs
    assert "wind_v_component_10m" in gfs
    assert "soil_temperature_0cm" not in gfs
    assert "soil_moisture_0_to_1cm" not in gfs

    assert "pm2_5" in cams
    assert "pm10" in cams
    assert "dust" in cams
    assert "us_aqi" in cams
    assert "european_aqi" in cams


def test_batch_summary_stops_after_third_failed_group():
    runner = load_module()
    batches = [
        {"batch": 1, "passed": False},
        {"batch": 2, "passed": True},
        {"batch": 3, "passed": False},
        {"batch": 4, "passed": False},
    ]

    summary = runner.summarize_batch_results(
        batches,
        planned_batches=100,
        frames=24,
        points_per_batch=10,
        failure_limit=3,
        variables_by_scope={"gfs": ["temperature_2m"], "cams": ["pm2_5"]},
    )

    assert summary["passed"] is False
    assert summary["frames"] == 24
    assert summary["planned_points"] == 1000
    assert summary["completed_batches"] == 4
    assert summary["failed_batches"] == 3
    assert summary["stopped_reason"] == "failure_limit_reached"
