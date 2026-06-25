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

    assert inventory["air_quality"]["domain"] == "cams_global"
    assert "pm2_5" in inventory["air_quality"]["raw_variables"]
    assert "aerosol_optical_depth" in inventory["air_quality"]["raw_variables"]
    assert "us_aqi" in inventory["air_quality"]["derived_variables"]
    assert "european_aqi" in inventory["air_quality"]["derived_variables"]
    assert "is_day" in inventory["air_quality"]["derived_variables"]


def test_inventory_records_minimum_runtime_download_domains():
    inventory = load_module().build_inventory(ROOT)

    commands = inventory["runtime_download_requirements"]
    assert {"command": "download-gfs", "domain": "gfs013", "levels": "surface"} in commands
    assert {"command": "download-gfs", "domain": "gfs025", "levels": "surface"} in commands
    assert {"command": "download-gfs", "domain": "gfs025", "levels": "surface+upper"} in commands
    assert {"command": "download-cams", "domain": "cams_global", "levels": "surface"} in commands

