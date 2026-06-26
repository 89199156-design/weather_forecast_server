import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "run_openmeteo_layer_target_validation.py"
    spec = importlib.util.spec_from_file_location("run_openmeteo_layer_target_validation", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_layer_point_batches_cover_1000_unique_grid_points():
    runner = load_module()
    grid = {
        "grid_width": 200,
        "grid_height": 100,
        "latitude_values": [float(index) for index in range(100)],
        "longitude_values": [float(index) for index in range(200)],
    }

    batches = runner.build_layer_point_batches(
        grid=grid,
        batches=100,
        points_per_batch=10,
        point_offset=0.0,
    )

    assert len(batches) == 100
    assert {len(batch) for batch in batches} == {10}
    points = [point for batch in batches for point in batch]
    assert len(points) == 1000
    assert len({runner.point_key(point) for point in points}) == 1000
    assert points[0]["flat"] >= 0
    assert points[-1]["flat"] < 20000


def test_layer_summary_stops_after_third_failed_batch():
    runner = load_module()
    summary = runner.summarize_batch_results(
        [
            {"passed": False, "checked_values": 10},
            {"passed": True, "checked_values": 10},
            {"passed": False, "checked_values": 10},
            {"passed": False, "checked_values": 10},
        ],
        planned_batches=100,
        points_per_batch=10,
        frames=24,
        failure_limit=3,
        layers=["pm2_5"],
    )

    assert summary["passed"] is False
    assert summary["completed_batches"] == 4
    assert summary["failed_batches"] == 3
    assert summary["stopped_reason"] == "failure_limit_reached"
    assert summary["checked_values"] == 40
