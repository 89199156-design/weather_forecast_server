import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "openmeteo_api_inventory.py"
    spec = importlib.util.spec_from_file_location("openmeteo_api_inventory", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_inventory_extracts_forecast_gfs_and_cams_variables_from_vendored_source():
    inventory = load_module().build_inventory(ROOT)

    assert inventory["forecast"]["model"] == "gfs_global"
    assert "uv_index" in inventory["forecast"]["surface_api_variables"]
    assert "uv_index_clear_sky" in inventory["forecast"]["surface_api_variables"]
    assert "visibility" in inventory["forecast"]["surface_api_variables"]
    assert "weather_code" in inventory["forecast"]["surface_api_variables"]
    assert "wind_speed_850hPa" in inventory["forecast"]["pressure_api_variables"]
    assert "relative_humidity_850hPa" in inventory["forecast"]["pressure_api_variables"]
    assert "visibility" in inventory["gfs_runtime_data"]["surface_variables"]
    assert "cape" in inventory["gfs_runtime_data"]["surface_variables"]
    assert "temperature_850hPa" in inventory["gfs_runtime_data"]["pressure_variables"]
    assert "uv_index" in inventory["gfs_point_api"]["surface_variables"]
    assert "visibility" in inventory["gfs_point_api"]["surface_variables"]
    assert "weather_code" in inventory["gfs_point_api"]["surface_variables"]
    assert "wind_speed_10m" in inventory["gfs_point_api"]["surface_variables"]
    assert "rain" in inventory["gfs_point_api"]["surface_variables"]
    assert "snowfall" in inventory["gfs_point_api"]["surface_variables"]
    assert "wind_speed_850hPa" in inventory["gfs_point_api"]["pressure_variables"]
    assert "relative_humidity_850hPa" in inventory["gfs_point_api"]["pressure_variables"]
    assert "wind_u_component_10m" not in inventory["gfs_point_api"]["surface_variables"]
    assert "wind_v_component_10m" not in inventory["gfs_point_api"]["surface_variables"]
    assert "categorical_freezing_rain" not in inventory["gfs_point_api"]["surface_variables"]
    assert "frozen_precipitation_percent" not in inventory["gfs_point_api"]["surface_variables"]
    assert "pm10" not in inventory["gfs_point_api"]["surface_variables"]
    assert "wave_height" not in inventory["gfs_point_api"]["surface_variables"]

    assert inventory["air_quality"]["domain"] == "cams_global"
    assert "pm2_5" in inventory["air_quality"]["raw_variables"]
    assert "pm10" in inventory["air_quality"]["raw_variables"]
    assert "sulphur_dioxide" in inventory["air_quality"]["raw_variables"]
    assert "nitrogen_dioxide" in inventory["air_quality"]["raw_variables"]
    assert "ozone" in inventory["air_quality"]["raw_variables"]
    assert "carbon_monoxide" in inventory["air_quality"]["raw_variables"]
    assert "aerosol_optical_depth" in inventory["air_quality"]["raw_variables"]
    assert "us_aqi" in inventory["air_quality"]["derived_variables"]
    assert "european_aqi" in inventory["air_quality"]["derived_variables"]
    assert "china_aqi" not in inventory["air_quality"]["derived_variables"]
    # ch_aqi is a project-owned Rust API extension, not an upstream Swift
    # Open-Meteo variable and therefore is intentionally absent here.
    assert "ch_aqi" not in inventory["air_quality"]["derived_variables"]
    assert not any(
        variable.startswith("ch_iaqi_")
        for variable in inventory["air_quality"]["derived_variables"]
    )
    assert not any(
        variable.startswith("ch_aqi_")
        for variable in inventory["air_quality"]["derived_variables"]
    )
    assert "is_day" in inventory["air_quality"]["derived_variables"]


def test_inventory_records_minimum_runtime_download_domains():
    inventory = load_module().build_inventory(ROOT)

    commands = inventory["runtime_download_requirements"]
    assert {"command": "download-gfs", "domain": "gfs013", "levels": "surface"} in commands
    assert {"command": "download-gfs", "domain": "gfs025", "levels": "surface"} in commands
    assert {"command": "download-gfs", "domain": "gfs025", "levels": "surface+upper"} in commands
    assert {"command": "download-cams", "domain": "cams_global", "levels": "surface"} in commands
