import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


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
    assert validator.values_match(22.98499870300293, 22.99, scale=100.0)
    assert validator.values_match(23.684993743896484, 23.69, scale=100.0)
    assert validator.values_match(87541.49780273438, 87542.0, scale=1.0)
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


def test_grid_helpers_support_lightweight_north_to_south_manifest():
    validator = load_module()

    grid = {
        "width": 3,
        "height": 2,
        "row_order": "north_to_south",
        "dx": 1.0,
        "dy": 1.0,
        "sample_bounds": {
            "lat_min": 10.0,
            "lat_max": 11.0,
            "lon_min": 100.0,
            "lon_max": 102.0,
        },
    }

    assert validator.grid_center(grid, y=0, x=2) == (11.0, 102.0)
    assert validator.grid_center(grid, y=1, x=0) == (10.0, 100.0)
    assert validator.grid_index(grid, lat=11.0, lon=102.0) == (0, 2)
    assert validator.grid_index(grid, lat=10.0, lon=100.0) == (1, 0)


def test_grid_helpers_reconstruct_points_from_bounds_instead_of_rounded_dx():
    validator = load_module()

    grid = {
        "width": 597,
        "height": 1,
        "row_order": "north_to_south",
        "dx": 0.117188,
        "dy": 1.0,
        "sample_bounds": {
            "lat_min": 10.0,
            "lat_max": 10.0,
            "lon_min": 70.078125,
            "lon_max": 139.921875,
        },
    }

    assert validator.grid_center(grid, y=0, x=591) == (10.0, 139.3359375)


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


def test_export_validation_supports_lightweight_manifest_without_http(tmp_path):
    validator = load_module()
    builder_spec = importlib.util.spec_from_file_location("build_webp", ROOT / "scripts" / "build_webp.py")
    builder = importlib.util.module_from_spec(builder_spec)
    assert builder_spec.loader is not None
    sys.modules[builder_spec.name] = builder
    builder_spec.loader.exec_module(builder)

    layer_dir = tmp_path / "layers"
    (layer_dir / "t2m").mkdir(parents=True)
    manifest = {
        "generated_at": 1000,
        "source": "gfs",
        "batch": 1000,
        "frame_count": 1,
        "frame_step_seconds": 3600,
        "file_pattern": "{timestamp}_{batch}.webp",
        "files": [1000],
        "grid": {
            "width": 1,
            "height": 1,
            "row_order": "north_to_south",
            "dx": 1.0,
            "dy": 1.0,
            "sample_bounds": {
                "lat_min": 10.0,
                "lat_max": 10.0,
                "lon_min": 100.0,
                "lon_max": 100.0,
            },
        },
    }
    (layer_dir / "gfs013_surface_data.json").write_text(json.dumps(manifest), encoding="utf-8")
    rgba = builder.encode_scalar_rgba(np.array([[12.34]], dtype=np.float32), vmin=-100.0, scale=100.0)
    Image.fromarray(rgba, mode="RGBA").save(layer_dir / "t2m" / "1000_1000.webp", "WEBP", lossless=True, quality=100)

    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "metadata.json").write_text(
        json.dumps(
            {
                "layout": "point_time",
                "scope": "gfs",
                "model": "gfs_global",
                "run": None,
                "points": [{"latitude": 10.00003, "longitude": 100.00003}],
                "times": [1000],
                "variables": ["temperature_2m"],
            }
        ),
        encoding="utf-8",
    )
    np.asarray([[12.34]], dtype=np.float32).tofile(export_dir / "temperature_2m.float32")

    report = validator.verify_layers_against_export(
        layer_dir=layer_dir,
        export_dir=export_dir,
        max_points=1,
        max_times=1,
        layers_filter="t2m",
    )

    assert report["mode"] == "export"
    assert report["checked_values"] == 1
    assert report["mismatch_count"] == 0


def test_prepare_point_export_request_uses_lightweight_manifest_grid_and_layer_variables(tmp_path):
    validator = load_module()

    layer_dir = tmp_path / "layers"
    layer_dir.mkdir()
    manifest = {
        "generated_at": 1000,
        "source": "gfs",
        "batch": 1000,
        "frame_count": 1,
        "frame_step_seconds": 3600,
        "file_pattern": "{timestamp}_{batch}.webp",
        "files": [1000],
        "grid": {
            "width": 2,
            "height": 1,
            "row_order": "north_to_south",
            "dx": 1.0,
            "dy": 1.0,
            "sample_bounds": {
                "lat_min": 10.0,
                "lat_max": 10.0,
                "lon_min": 100.0,
                "lon_max": 101.0,
            },
        },
    }
    (layer_dir / "gfs013_surface_data.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload = validator.point_export_request_payload(
        layer_dir=layer_dir,
        manifest_name=None,
        max_points=2,
        layers_filter="t2m,wind",
    )

    assert payload["scope"] == "gfs"
    assert payload["model"] == "gfs_global"
    assert payload["start_hour"] == "1970-01-01T00:00"
    assert payload["end_hour"] == "1970-01-01T00:00"
    assert payload["points"] == [
        {"latitude": 10.0, "longitude": 100.0},
        {"latitude": 10.0, "longitude": 101.0},
    ]
    assert payload["variables"] == ["temperature_2m", "wind_u_component_10m", "wind_v_component_10m"]


def test_decode_scalar_and_wind_pixels_match_builder_encoding():
    validator = load_module()
    builder_spec = importlib.util.spec_from_file_location("build_webp", ROOT / "scripts" / "build_webp.py")
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
