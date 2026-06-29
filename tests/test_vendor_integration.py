from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_vendor(path: str) -> str:
    return (ROOT / "vendor" / "open-meteo" / path).read_text(encoding="utf-8")


def test_openmeteo_package_uses_upstream_sdk_dependency():
    package = read_vendor("Package.swift")

    assert 'url: "https://github.com/open-meteo/sdk.git", from: "1.26.0"' in package
    assert '.package(path: "../openmeteo-sdk")' not in package
    assert '.product(name: "OpenMeteoSdk", package: "sdk")' in package
    assert '.product(name: "OpenMeteoSdk", package: "openmeteo-sdk")' not in package


def test_vendored_openmeteo_only_patches_download_transport_and_region_grid():
    domain = read_vendor("Sources/App/Gfs/GfsDomain.swift")
    download = read_vendor("Sources/App/Gfs/GfsDownload.swift")
    cams_domain = read_vendor("Sources/App/Cams/CamsDomain.swift")
    cams_download = read_vendor("Sources/App/Cams/CamsDownload.swift")
    curl = read_vendor("Sources/App/Helper/Download/Curl.swift")

    forbidden_tokens = (
        "gfsNoaaDownloadHeaders",
        "roundToGribDecimalScale",
        "decimalScaleFactor",
        "GfsController",
        "weather_code",
    )

    combined = "\n".join((domain, download, curl))
    for token in forbidden_tokens:
        assert token not in combined

    assert "WeatherForecastServerSourceConfig" in domain
    assert "WEATHER_GFS_FILTER_DOWNLOAD" in domain
    assert "GfsFilterDownload" in download
    assert "downloadFilteredIndexedGrib" in download
    assert "regularGridSlice" in domain
    assert "WEATHER_CAMS_AREA_DOWNLOAD" in domain
    assert "downloadCamsGlobalArea" in cams_download
    assert "cams-global-atmospheric-composition-forecasts" in cams_download
    assert "getCamsGlobalAreaApiName" in cams_domain


def test_vendored_openmeteo_uses_upstream_remote_data_directory_contract():
    configure = read_vendor("Sources/App/configure.swift")
    om_file_type = read_vendor("Sources/App/Helper/File/OmFileType.swift")

    assert "REMOTE_DATA_DIRECTORY" in configure
    assert "remoteDataDirectory" in configure
    assert "WEATHER_DEM_REMOTE_DATA_DIRECTORY" not in configure
    assert "demRemoteDataDirectory" not in configure
    assert "OpenMeteo.demRemoteDataDirectory" not in om_file_type
    assert 'remoteDirectory.replacingOccurrences(of: "data", with: "data_run")' in om_file_type


def test_openmeteo_raw_download_is_the_default_runtime_data_mode():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")
    singapore_env = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    assert "WEATHER_GFS_DOWNLOAD_MODE=raw" in singapore_env
    assert "WEATHER_OPENMETEO_SYNC_BASE_URL" in singapore_env
    assert "REMOTE_DATA_DIRECTORY=" in singapore_env
    assert "CACHE_SIZE=10GB" in singapore_env
    assert "Production uses `raw`" in singapore_env
    assert "local `.om` database" in singapore_env
    assert "sync_openmeteo_database ncep_gfs013" in script
    assert "sync_openmeteo_database ncep_gfs025" in script
    assert "Open-Meteo downloader convert original source files into local `.om` chunks" in script
