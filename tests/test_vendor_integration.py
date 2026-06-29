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


def test_vendored_openmeteo_does_not_fork_gfs_domain_or_download_logic():
    domain = read_vendor("Sources/App/Gfs/GfsDomain.swift")
    download = read_vendor("Sources/App/Gfs/GfsDownload.swift")
    curl = read_vendor("Sources/App/Helper/Download/Curl.swift")

    forbidden_tokens = (
        "WeatherForecastServerSourceConfig",
        "WEATHER_GFS_NOMADS_BASE_URL",
        "WEATHER_GFS_AWS_BASE_URL",
        "WEATHER_GFS_FILTER_DOWNLOAD",
        "WEATHER_GFS_FILTER_0P25_URL",
        "WEATHER_GFS_FILTER_0P25B_URL",
        "WEATHER_GFS_FILTER_SFLUX_URL",
        "GfsFilterDownload",
        "downloadFilteredIndexedGrib",
        "downloadFilteredIndexAndDecode",
        "gfsNoaaDownloadHeaders",
    )

    combined = "\n".join((domain, download, curl))
    for token in forbidden_tokens:
        assert token not in combined


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
