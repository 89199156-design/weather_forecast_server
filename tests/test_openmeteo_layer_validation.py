import importlib.util
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_openmeteo_layers.py"


def load_module():
    spec = importlib.util.spec_from_file_location("validate_openmeteo_layers", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_evenly_spaced_flat_indices_cover_first_and_last_points():
    validator = load_module()

    assert validator.evenly_spaced_flat_indices(total=10, max_points=4) == [0, 3, 6, 9]
    assert validator.evenly_spaced_flat_indices(total=3, max_points=5) == [0, 1, 2]


def test_layer_api_value_transform_uses_manifest_multiplier():
    validator = load_module()

    layer = {"api_multiplier": 100.0}

    assert validator.transform_api_value(1013.2, layer) == 101320.0
    assert validator.transform_api_value(None, layer) is None
    assert validator.transform_api_value(float("nan"), layer) is None


def test_layer_api_value_transform_derives_phase_and_thunderstorm_from_weather_code():
    validator = load_module()

    phase_layer = {"derive": "precip_phase_from_weather_code", "api_multiplier": 1.0}
    thunderstorm_layer = {"derive": "thunderstorm_code_from_weather_code", "api_multiplier": 1.0}

    assert validator.transform_api_value(61, phase_layer) == 1.0
    assert validator.transform_api_value(71, phase_layer) == 2.0
    assert validator.transform_api_value(56, phase_layer) == 4.0
    assert validator.transform_api_value(95, phase_layer) == 0.0
    assert validator.transform_api_value(96, phase_layer) == 0.0
    assert validator.transform_api_value(99, phase_layer) == 0.0
    assert validator.transform_api_value(3, phase_layer) == 0.0
    assert validator.transform_api_value(None, phase_layer) is None

    assert validator.transform_api_value(95, thunderstorm_layer) == 95.0
    assert validator.transform_api_value(96, thunderstorm_layer) == 96.0
    assert validator.transform_api_value(99, thunderstorm_layer) == 99.0
    assert validator.transform_api_value(61, thunderstorm_layer) == 0.0


def test_value_comparison_uses_encoding_precision():
    validator = load_module()

    assert validator.values_match(10.0, 10.004, scale=100.0)
    assert not validator.values_match(10.0, 10.02, scale=100.0)
    assert validator.values_match(None, None, scale=100.0)
    assert not validator.values_match(None, 0.0, scale=100.0)


def test_grid_index_uses_manifest_lat_lon_values():
    validator = load_module()

    grid = {
        "grid_width": 3,
        "grid_height": 2,
        "latitude_values": [10.0, 11.0],
        "longitude_values": [100.0, 101.0, 102.0],
    }

    assert validator.grid_index(grid, lat=10.2, lon=101.6) == (0, 2)
    assert validator.grid_center(grid, y=1, x=0) == (11.0, 100.0)


def test_manifest_path_prefers_gfs_then_cams(tmp_path):
    validator = load_module()

    layer_dir = tmp_path / "layers"
    layer_dir.mkdir()
    cams = layer_dir / "cams_global_data.json"
    cams.write_text("{}", encoding="utf-8")
    assert validator.manifest_path_for_layer_dir(layer_dir, None) == cams

    gfs = layer_dir / "gfs013_surface_data.json"
    gfs.write_text("{}", encoding="utf-8")
    assert validator.manifest_path_for_layer_dir(layer_dir, None) == gfs
    assert validator.manifest_path_for_layer_dir(layer_dir, "cams_global_data.json") == cams


def test_decode_scalar_and_wind_pixels_match_builder_encoding():
    validator = load_module()
    builder_spec = importlib.util.spec_from_file_location("build_openmeteo_layers", ROOT / "scripts" / "build_openmeteo_layers.py")
    builder = importlib.util.module_from_spec(builder_spec)
    assert builder_spec.loader is not None
    sys.modules[builder_spec.name] = builder
    builder_spec.loader.exec_module(builder)

    scalar = np.array([[12.34]], dtype=np.float32)
    rgba = builder.encode_scalar_rgba(scalar, vmin=-100.0, scale=100.0)
    assert np.isclose(validator.decode_scalar_pixel(rgba[0, 0], vmin=-100.0, scale=100.0), 12.34)

    wind_rgba = builder.encode_wind_rgba(
        np.array([[3.2]], dtype=np.float32),
        np.array([[-1.2]], dtype=np.float32),
    )
    u, v = validator.decode_wind_pixel(wind_rgba[0, 0])
    assert np.isclose(u, 3.2)
    assert np.isclose(v, -1.2)
