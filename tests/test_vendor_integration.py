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
    assert "downloadFilteredIndexAndDecode" in download
    assert "downloadGrib(url: url, bzip2Decode: false)" in download
    assert "RegularGrid(" in domain


def test_openmeteo_processed_database_sync_is_the_parity_download_mode():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")
    singapore_env = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    assert "WEATHER_GFS_DOWNLOAD_MODE=sync" in singapore_env
    assert "WEATHER_OPENMETEO_SYNC_BASE_URL" in singapore_env
    assert "REMOTE_DATA_DIRECTORY=" in singapore_env
    assert "CACHE_SIZE=10GB" in singapore_env
    assert "Open-Meteo's processed `.om`" in singapore_env
    assert "sync_openmeteo_database ncep_gfs013" in script
    assert "sync_openmeteo_database ncep_gfs025" in script
    assert "NOAA raw/filter conversion is not the parity baseline" in script


def test_gfs_filtered_download_maps_noaa_filter_messages_back_to_openmeteo_index_matches():
    download = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDownload.swift").read_text(
        encoding="utf-8"
    )

    assert "downloadFilteredIndexAndDecode" in download
    assert "filteredLineIndexes" in download
    assert "indexLineMatchesFilter" in download
    assert "messages[position]" in download


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


def test_gfs_region_filtered_grids_are_not_shifted_as_global_grids():
    download = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDownload.swift").read_text(
        encoding="utf-8"
    )

    regional_branch = download.index("if GfsFilterDownload.usesRegionalGrid(domain: domain)")
    global_branch = download.index("else if isGlobal")
    assert regional_branch < global_branch
    assert "flipLatitude()" not in download[regional_branch:global_branch]

    regional_domain_branch = download.index("if GfsFilterDownload.usesRegionalGrid(domain: domain)", regional_branch + 1)
    global_domain_branch = download.index("else if domain.isGlobal")
    assert regional_domain_branch < global_domain_branch
    assert "flipLatitude()" not in download[regional_domain_branch:global_domain_branch]


def test_gfs_download_imports_eccodes_when_using_grib_message_type():
    download = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDownload.swift").read_text(
        encoding="utf-8"
    )

    assert "message: GribMessage" in download
    assert "import SwiftEccodes" in download


def test_dem_remote_directory_is_separate_from_forecast_remote_archive():
    configure = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "configure.swift").read_text(
        encoding="utf-8"
    )
    om_file_type = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Helper" / "File" / "OmFileType.swift").read_text(
        encoding="utf-8"
    )
    singapore_env = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    assert "WEATHER_DEM_REMOTE_DATA_DIRECTORY" in configure
    assert "demRemoteDataDirectory" in configure
    assert "domain == .copernicus_dem90" in om_file_type
    assert "OpenMeteo.demRemoteDataDirectory" in om_file_type
    assert "WEATHER_DEM_REMOTE_DATA_DIRECTORY" in singapore_env


def test_filtered_regional_grib_values_keep_openmeteo_decoded_precision():
    download = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDownload.swift").read_text(
        encoding="utf-8"
    )

    assert "roundToGribDecimalScale" not in download
    assert 'message.get(attribute: "decimalScaleFactor")' not in download

    regional_branch = download.index("if GfsFilterDownload.usesRegionalGrid(domain: domain)")
    global_branch = download.index("else if isGlobal")
    assert "shift180LongitudeAndFlipLatitude" not in download[regional_branch:global_branch]

    regional_domain_branch = download.index("if GfsFilterDownload.usesRegionalGrid(domain: domain)", regional_branch + 1)
    global_domain_branch = download.index("else if domain.isGlobal")
    assert "shift180LongitudeAndFlipLatitude" not in download[regional_domain_branch:global_domain_branch]
