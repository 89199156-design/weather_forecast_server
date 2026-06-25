import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "validate_openmeteo_point_api.py"
    spec = importlib.util.spec_from_file_location("validate_openmeteo_point_api", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_generate_points_is_deterministic_inside_configured_region():
    validator = load_module()

    points = validator.generate_points(3, left_lon=70, right_lon=140, bottom_lat=0, top_lat=58)

    assert points == [
        {"latitude": 9.666667, "longitude": 81.666667},
        {"latitude": 29.0, "longitude": 105.0},
        {"latitude": 48.333333, "longitude": 128.333333},
    ]


def test_chunked_inventory_keeps_gfs_and_cams_variables_separate():
    validator = load_module()
    inventory = {
        "forecast": {
            "surface_api_variables": ["temperature_2m", "uv_index"],
            "pressure_api_variables": ["temperature_850hPa"],
        },
        "air_quality": {
            "raw_variables": ["pm2_5"],
            "derived_variables": ["us_aqi"],
        },
    }

    assert validator.variables_for_scope(inventory, "gfs") == ["temperature_2m", "uv_index", "temperature_850hPa"]
    assert validator.variables_for_scope(inventory, "cams") == ["pm2_5", "us_aqi"]
    assert validator.chunked(["a", "b", "c"], 2) == [["a", "b"], ["c"]]


def test_compare_series_reports_value_null_and_length_mismatches():
    validator = load_module()

    assert validator.compare_series([1.0, None, 3.0], [1.00001, None, 3.0], frames=3, tolerance=0.001) == []
    mismatches = validator.compare_series([1.0, None, 3.0], [2.0, 4.0], frames=3, tolerance=0.001)

    assert mismatches == [
        {"frame": 0, "local": 1.0, "reference": 2.0, "reason": "value_mismatch"},
        {"frame": 1, "local": None, "reference": 4.0, "reason": "null_mismatch"},
        {"frame": 2, "local": 3.0, "reference": None, "reason": "length_mismatch"},
    ]


def test_summarize_variable_detects_missing_and_all_null_local_output():
    validator = load_module()

    assert validator.summarize_variable(None, frames=2) == {"status": "missing", "frames": 0, "nulls": 2}
    assert validator.summarize_variable([None, None, 3], frames=2) == {"status": "all_null", "frames": 2, "nulls": 2}
    assert validator.summarize_variable([None, 1, 2], frames=2) == {"status": "ok", "frames": 2, "nulls": 1}

