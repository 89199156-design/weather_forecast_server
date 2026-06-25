import importlib.util
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_openmeteo_layers.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_openmeteo_layers", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_gfs013_region_grid_matches_openmeteo_swift_slice():
    layers = load_module()

    grid = layers.compute_gfs013_region_grid(
        left_lon=70.0,
        right_lon=140.0,
        bottom_lat=0.0,
        top_lat=58.0,
    )

    assert grid.width == 597
    assert grid.height == 495
    assert grid.row_order == "south_to_north"
    assert grid.longitude_values[0] == 70.078125
    assert grid.longitude_values[-1] == 139.921875
    assert grid.latitude_values[0] == 0.058575
    assert grid.latitude_values[-1] == 57.930354


def test_layer_definitions_are_api_variables_not_legacy_grib_fields():
    layers = load_module()

    api_variables = set(layers.required_api_variables(layers.DEFAULT_LAYER_DEFINITIONS))

    assert api_variables == {
        "cloud_cover",
        "cloud_cover_high",
        "cloud_cover_mid",
        "cloud_cover_low",
        "temperature_2m",
        "relative_humidity_2m",
        "wind_u_component_10m",
        "wind_v_component_10m",
        "precipitation",
        "snow_depth",
        "wind_gusts_10m",
        "visibility",
        "pressure_msl",
    }

    source = SCRIPT.read_text(encoding="utf-8")
    for legacy_dependency in (
        "cfgrib",
        "eccodes",
        "gfs_raw_download_core",
        "gfs013_surface_core",
        "gfs_core",
    ):
        assert legacy_dependency not in source


def test_layer_manifest_preserves_encoder_vmin_for_decoding():
    layers = load_module()
    grid = layers.compute_gfs013_region_grid(
        left_lon=70.0,
        right_lon=140.0,
        bottom_lat=0.0,
        top_lat=58.0,
    )

    manifests = {layer.name: layer.manifest(grid) for layer in layers.DEFAULT_LAYER_DEFINITIONS}

    assert manifests["cloud_total_1"]["vmin"] == 0.0
    assert manifests["t2m"]["vmin"] == -100.0
    assert manifests["prmsl"]["vmin"] == 50000.0


def test_scalar_and_wind_encoding_round_trip():
    layers = load_module()

    scalar = np.array([[np.nan, -1.23], [0.0, 12.34]], dtype=np.float32)
    rgba = layers.encode_scalar_rgba(scalar, vmin=-100.0, scale=100.0)
    decoded = layers.decode_scalar_rgba(rgba, vmin=-100.0, scale=100.0)

    assert np.isnan(decoded[0, 0])
    assert np.isclose(decoded[0, 1], -1.23)
    assert np.isclose(decoded[1, 0], 0.0)
    assert np.isclose(decoded[1, 1], 12.34)

    u = np.array([[np.nan, -4.7], [0.0, 3.2]], dtype=np.float32)
    v = np.array([[1.0, -1.2], [0.0, 9.9]], dtype=np.float32)
    wind_rgba = layers.encode_wind_rgba(u, v)
    decoded_u, decoded_v = layers.decode_wind_rgba(wind_rgba)

    assert np.isnan(decoded_u[0, 0])
    assert np.isnan(decoded_v[0, 0])
    assert np.isclose(decoded_u[0, 1], -4.7)
    assert np.isclose(decoded_v[0, 1], -1.2)
    assert np.isclose(decoded_u[1, 1], 3.2)
    assert np.isclose(decoded_v[1, 1], 9.9)


def test_api_request_params_preserve_openmeteo_point_semantics():
    layers = load_module()

    params = layers.build_forecast_params(
        latitudes=[31.23, 23.13],
        longitudes=[121.47, 113.26],
        variables=["temperature_2m", "wind_u_component_10m"],
        model="gfs013",
        start_hour="2026-06-25T07:00",
        end_hour="2026-06-27T08:00",
    )

    assert params["latitude"] == "31.230000,23.130000"
    assert params["longitude"] == "121.470000,113.260000"
    assert params["hourly"] == "temperature_2m,wind_u_component_10m"
    assert params["models"] == "gfs013"
    assert params["timezone"] == "UTC"
    assert params["cell_selection"] == "land"
    assert "elevation" not in params
