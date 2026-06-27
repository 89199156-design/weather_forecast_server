import importlib.util
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_openmeteo_point_package.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_openmeteo_point_package", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_point_package_variables_are_openmeteo_api_variables():
    points = load_module()

    variables = points.required_variables(points.POINT_FIELDS)

    assert "weather_code" in variables
    assert "temperature_2m" in variables
    assert "wind_u_component_10m" in variables
    assert "uv_index" in variables
    assert "crain" not in variables
    assert "cfrzr" not in variables
    assert "csnow" not in variables


def test_point_package_fields_include_client_and_layer_alignment_fields():
    points = load_module()

    field_names = [field.name for field in points.POINT_FIELDS]

    for name in (
        "weather_code",
        "precip_phase_code",
        "thunderstorm_code",
        "temperature_c",
        "dew_point_c",
        "humidity_pct",
        "u10_ms",
        "v10_ms",
        "visibility_m",
        "cape_jkg",
        "uv_index",
    ):
        assert name in field_names


def test_point_package_uses_north_to_south_grid_rows():
    points = load_module()
    grid = points.layer_builder.compute_gfs013_region_grid(
        left_lon=70.0,
        right_lon=140.0,
        bottom_lat=0.0,
        top_lat=58.0,
    )

    assert grid.row_order == "north_to_south"
    assert grid.latitude_values[0] > grid.latitude_values[-1]
    assert grid.manifest()["sample_bounds"]["lat_min"] == 0.058575
    assert grid.manifest()["sample_bounds"]["lat_max"] == 57.930354


def test_point_package_derives_phase_and_thunderstorm_from_weather_code():
    points = load_module()
    variables = {
        "weather_code": np.asarray([[0, 61, 95, 99]], dtype=np.float32),
    }

    phase_field = next(field for field in points.POINT_FIELDS if field.name == "precip_phase_code")
    thunder_field = next(field for field in points.POINT_FIELDS if field.name == "thunderstorm_code")

    np.testing.assert_array_equal(points.derive_values(phase_field, variables), [[0, 1, 0, 0]])
    np.testing.assert_array_equal(points.derive_values(thunder_field, variables), [[0, 0, 95, 99]])


def test_point_package_phase_metadata_only_lists_generated_codes():
    points = load_module()

    assert points.PRECIP_PHASE_TEXTS == {
        0: "无明显降水",
        1: "雨",
        2: "雪",
        4: "冻雨风险",
    }
