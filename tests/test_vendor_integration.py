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


def test_gfs_region_filter_download_is_configurable_and_reuses_openmeteo_pipeline():
    domain = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDomain.swift").read_text(
        encoding="utf-8"
    )
    download = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDownload.swift").read_text(
        encoding="utf-8"
    )
    singapore_env = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    for key in (
        "WEATHER_GFS_FILTER_DOWNLOAD",
        "WEATHER_GFS_FILTER_0P25_URL",
        "WEATHER_GFS_FILTER_SFLUX_URL",
        "WEATHER_REGION_LEFT_LON",
        "WEATHER_REGION_RIGHT_LON",
        "WEATHER_REGION_BOTTOM_LAT",
        "WEATHER_REGION_TOP_LAT",
    ):
        assert key in domain or key in download or key in singapore_env

    assert "GfsFilterDownload.filteredUrls" in download
    assert "downloadFilteredIndexedGrib" in download
    assert "downloadIndexAndDecode" in download
    assert "downloadGrib(url: url, bzip2Decode: false)" in download
    assert "RegularGrid(" in domain


def test_gfs_region_grid_uses_source_grid_point_centers():
    domain = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDomain.swift").read_text(
        encoding="utf-8"
    )

    assert "regularGridSlice" in domain
    assert "ceil((region.leftLon - lonMin) / dx" in domain
    assert "floor((region.rightLon - lonMin) / dx" in domain
    assert "fullNx: 3072" in domain
    assert "fullNy: 1536" in domain
    assert "fullNx: 1440" in domain
    assert "fullNy: 721" in domain
    assert "gridPointCount(lower: region.leftLon" not in domain


def test_gfs_download_imports_eccodes_when_using_grib_message_type():
    download = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDownload.swift").read_text(
        encoding="utf-8"
    )

    assert "message: GribMessage" in download
    assert "import SwiftEccodes" in download
