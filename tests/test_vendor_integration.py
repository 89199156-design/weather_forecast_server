from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_openmeteo_uses_vendored_sdk_path():
    package = (ROOT / "vendor" / "open-meteo" / "Package.swift").read_text(encoding="utf-8")

    assert '.package(path: "../openmeteo-sdk")' in package
    assert '.product(name: "OpenMeteoSdk", package: "openmeteo-sdk")' in package
    assert 'https://github.com/open-meteo/sdk.git", from: "1.27.2"' not in package


def test_gfs_download_base_urls_are_environment_configurable():
    source = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDomain.swift").read_text(
        encoding="utf-8"
    )

    for key in (
        "WEATHER_GFS_NOMADS_BASE_URL",
        "WEATHER_GFS_AWS_BASE_URL",
        "WEATHER_GEFS_NOMADS_BASE_URL",
        "WEATHER_GEFS_AWS_BASE_URL",
        "WEATHER_HRRR_NOMADS_BASE_URL",
        "WEATHER_HRRR_AWS_BASE_URL",
        "WEATHER_NAM_NOMADS_BASE_URL",
    ):
        assert key in source

    assert "WeatherForecastServerSourceConfig.baseUrl" in source
