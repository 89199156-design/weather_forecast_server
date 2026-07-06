import importlib.util
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_webp.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_webp", SCRIPT)
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
    assert grid.row_order == "north_to_south"
    assert grid.longitude_values[0] == 70.078125
    assert grid.longitude_values[-1] == 139.921875
    assert grid.latitude_values[0] == 57.930354
    assert grid.latitude_values[-1] == 0.058575
    assert grid.manifest()["sample_bounds"] == {
        "lon_min": 70.078125,
        "lat_min": 0.058575,
        "lon_max": 139.921875,
        "lat_max": 57.930354,
    }


def test_default_layer_model_uses_openmeteo_gfs_global_mixer():
    layers = load_module()

    assert layers.DEFAULT_LAYER_MODEL == "gfs_global"


def test_frame_stems_use_valid_timestamp_and_batch_timestamp_without_version_suffix():
    layers = load_module()

    stems = layers.frame_stems(
        ["2026-07-01T04:00", "2026-07-01T05:00"],
        "2026-07-01T04:00",
    )

    assert stems == [
        "1782878400_1782878400",
        "1782882000_1782878400",
    ]


def test_frame_timestamps_are_manifest_files():
    layers = load_module()

    timestamps = layers.frame_timestamps(["2026-07-01T04:00", "2026-07-01T05:00"])

    assert timestamps == [1782878400, 1782882000]


def test_grid_manifest_is_compact_without_coordinate_arrays():
    layers = load_module()
    grid = layers.compute_gfs013_region_grid(
        left_lon=70.0,
        right_lon=140.0,
        bottom_lat=0.0,
        top_lat=58.0,
    )

    manifest = grid.manifest()

    assert manifest["width"] == 597
    assert manifest["height"] == 495
    assert manifest["dx"] == 0.117188
    assert manifest["dy"] == 0.117149
    assert manifest["sample_bounds"] == {
        "lon_min": 70.078125,
        "lat_min": 0.058575,
        "lon_max": 139.921875,
        "lat_max": 57.930354,
    }
    assert manifest["display_bounds"] == {
        "lon_min": 70.019531,
        "lat_min": 0.0,
        "lon_max": 139.980469,
        "lat_max": 57.988929,
    }
    assert "longitude_values" not in manifest
    assert "latitude_values" not in manifest


def test_build_manifest_payload_is_minimal_batch_manifest():
    layers = load_module()
    grid = layers.compute_gfs013_region_grid(
        left_lon=70.0,
        right_lon=140.0,
        bottom_lat=0.0,
        top_lat=58.0,
    )

    manifest = layers.build_manifest_payload(
        scope="gfs",
        grid=grid,
        batch=1782878400,
        files=[1782878400, 1782882000],
        generated_at=1782879000,
    )

    assert manifest == {
        "generated_at": 1782879000,
        "source": "gfs",
        "batch": 1782878400,
        "frame_count": 2,
        "frame_step_seconds": 3600,
        "file_pattern": "{timestamp}_{batch}.webp",
        "files": [1782878400, 1782882000],
        "grid": grid.manifest(),
    }


def test_layer_catalog_file_matches_server_definitions():
    layers = load_module()

    catalog_path = ROOT / "config" / "weather_layer_catalog.json"
    assert catalog_path.exists()
    assert layers.load_layer_catalog(catalog_path) == layers.layer_catalog_payload()


def test_layer_catalog_records_layer_resolution_labels():
    layers = load_module()

    catalog = layers.layer_catalog_payload()
    gfs_layers = catalog["products"]["gfs"]["layers"]
    cams_layers = catalog["products"]["cams"]["layers"]

    assert gfs_layers["t2m"]["source_resolution"] == "13km"
    assert gfs_layers["tp"]["source_resolution"] == "13km"
    assert gfs_layers["uv_index"]["source_resolution"] == "13km"
    assert gfs_layers["vis"]["source_resolution"] == "28km"
    assert gfs_layers["gust"]["source_resolution"] == "28km"
    assert gfs_layers["cape"]["source_resolution"] == "28km"
    assert gfs_layers["prmsl"]["source_resolution"] == "28km"
    assert gfs_layers["sp"]["source_resolution"] == "28km(13+28)"
    assert gfs_layers["precip_phase"]["source_resolution"] == "28km(13+28)"
    assert gfs_layers["thunderstorm_code"]["source_resolution"] == "28km(13+28)"
    assert "resolution" not in gfs_layers["vis"]
    assert {layer["source_resolution"] for layer in cams_layers.values()} == {"44km"}


def test_layer_definitions_are_api_variables_not_legacy_grib_fields():
    layers = load_module()

    layer_names = tuple(layer.name for layer in layers.DEFAULT_LAYER_DEFINITIONS)
    api_variables = set(layers.required_api_variables(layers.DEFAULT_LAYER_DEFINITIONS))

    assert layer_names == (
        "cloud_total_1",
        "cloud_high_1",
        "cloud_mid_1",
        "cloud_low_1",
        "t2m",
        "d2m",
        "r2",
        "wind",
        "tp",
        "snod",
        "gust",
        "vis",
        "precip_phase",
        "thunderstorm_code",
        "cape",
        "prmsl",
        "sp",
        "uv_index",
    )
    assert api_variables == {
        "cloud_cover",
        "cloud_cover_high",
        "cloud_cover_mid",
        "cloud_cover_low",
        "temperature_2m",
        "dew_point_2m",
        "relative_humidity_2m",
        "wind_u_component_10m",
        "wind_v_component_10m",
        "precipitation",
        "snow_depth",
        "wind_gusts_10m",
        "visibility",
        "weather_code",
        "cape",
        "pressure_msl",
        "surface_pressure",
        "uv_index",
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


def test_cams_layer_definitions_cover_client_air_quality_layers():
    layers = load_module()

    layer_names = tuple(layer.name for layer in layers.CAMS_LAYER_DEFINITIONS)
    api_variables = set(layers.required_api_variables(layers.CAMS_LAYER_DEFINITIONS))

    assert layer_names == (
        "pm2_5",
        "pm10",
        "aerosol_optical_depth",
        "dust",
    )
    assert api_variables == {
        "pm2_5",
        "pm10",
        "aerosol_optical_depth",
        "dust",
    }
    assert all("aqi" not in name for name in layer_names)
    assert all("aqi" not in variable for variable in api_variables)
    assert layers.manifest_filename_for_scope("cams") == "cams_global_data.json"


def test_layer_catalog_preserves_encoder_vmin_for_decoding():
    layers = load_module()

    manifests = layers.layer_catalog_payload()["products"]["gfs"]["layers"]

    assert manifests["cloud_total_1"]["vmin"] == 0.0
    assert manifests["t2m"]["vmin"] == -100.0
    assert manifests["d2m"]["vmin"] == -100.0
    assert manifests["wind"]["vmin"] == -100.0
    assert manifests["wind"]["encoding"] == "uv"
    assert manifests["prmsl"]["vmin"] == 50000.0
    assert manifests["sp"]["vmin"] == 30000.0
    assert manifests["sp"]["scale"] == 0.5
    assert manifests["cape"]["unit"] == "J/kg"
    assert manifests["uv_index"]["unit"] == "index"
    assert "weather_code" not in manifests
    assert manifests["precip_phase"]["range"] == [0.0, 4.0]
    assert manifests["precip_phase"]["encoding"] == "categorical"
    assert manifests["thunderstorm_code"]["encoding"] == "categorical"
    assert manifests["tp"]["encoding"] == "scalar"


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


def test_cams_dust_encoding_round_trips_high_values_without_saturation():
    layers = load_module()

    dust_layer = next(layer for layer in layers.CAMS_LAYER_DEFINITIONS if layer.name == "dust")
    values = np.array([[6810.0, 6951.0]], dtype=np.float32)

    rgba = layers.encode_scalar_rgba(values, vmin=dust_layer.vmin, scale=dust_layer.scale)
    decoded = layers.decode_scalar_rgba(rgba, vmin=dust_layer.vmin, scale=dust_layer.scale)

    np.testing.assert_allclose(decoded, values, atol=0.5)


def test_openmeteo_weather_code_drives_phase_and_thunderstorm_layers():
    layers = load_module()

    weather_codes = np.array(
        [
            [0, 3, 61, 65],
            [71, 85, 56, 67],
            [95, 96, 99, 45],
        ],
        dtype=np.float32,
    )

    phase = layers.precip_phase_from_weather_code(weather_codes)
    thunderstorm = layers.thunderstorm_code_from_weather_code(weather_codes)

    np.testing.assert_array_equal(
        phase,
        np.array(
            [
                [0, 0, 1, 1],
                [2, 2, 4, 4],
                [0, 0, 0, 0],
            ],
            dtype=np.float32,
        ),
    )
    np.testing.assert_array_equal(
        thunderstorm,
        np.array(
            [
                [0, 0, 0, 0],
                [0, 0, 0, 0],
                [95, 96, 99, 0],
            ],
            dtype=np.float32,
        ),
    )


def test_export_request_payload_preserves_openmeteo_engine_inputs():
    layers = load_module()
    grid = layers.compute_gfs013_region_grid(
        left_lon=70.0,
        right_lon=70.2,
        bottom_lat=20.0,
        top_lat=20.2,
    )

    payload = layers.build_export_request_payload(
        scope="gfs",
        grid=grid,
        variables=["temperature_2m", "wind_u_component_10m"],
        model="gfs_global",
        domain=None,
        start_hour="2026-06-25T07:00",
        end_hour="2026-06-27T08:00",
        run="2026-06-25T06:00",
        chunk_size=12,
    )

    assert payload["scope"] == "gfs"
    assert payload["model"] == "gfs_global"
    assert payload["run"] == "2026-06-25T06:00"
    assert payload["start_hour"] == "2026-06-25T07:00"
    assert payload["end_hour"] == "2026-06-27T08:00"
    assert payload["variables"] == ["temperature_2m", "wind_u_component_10m"]
    assert payload["chunk_size"] == 12
    assert payload["width"] == grid.width
    assert payload["height"] == grid.height
    assert payload["latitudes"] == grid.latitude_values
    assert payload["longitudes"] == grid.longitude_values


def test_cams_export_request_payload_uses_air_quality_domain():
    layers = load_module()
    grid = layers.compute_gfs013_region_grid(
        left_lon=121.0,
        right_lon=121.2,
        bottom_lat=31.0,
        top_lat=31.2,
    )

    payload = layers.build_export_request_payload(
        scope="cams",
        grid=grid,
        variables=["pm2_5", "dust"],
        model=None,
        domain="cams_global",
        start_hour="2026-06-25T07:00",
        end_hour="2026-06-26T06:00",
    )

    assert payload["scope"] == "cams"
    assert payload["model"] == "cams_global"
    assert payload["run"] is None
    assert payload["variables"] == ["pm2_5", "dust"]
