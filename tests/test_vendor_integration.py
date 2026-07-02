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
    assert "GfsRegionalDownload" in download
    assert "decodeRegional" in download
    assert "downloadIndexedGrib" in download
    assert "downloadFilteredIndexedGrib" not in download
    assert "filter_gfs_sflux" not in download
    assert "WEATHER_GFS_FILTER" not in download
    assert "regularGridSlice" in domain
    assert "struct RegionalRegularGrid: Gridable" in domain
    assert "return base.getCoordinates(gridpoint: (y + y0) * base.nx + x + x0)" in domain
    assert domain.count("return RegionalRegularGrid(base: base, x0: slice.x0, y0: slice.y0, nx: slice.nx, ny: slice.ny)") == 2
    assert "let base = RegularGrid(nx: 1440, ny: 721, latMin: -90, lonMin: -180, dx: 0.25, dy: 0.25)" in domain
    assert domain.count("haloCells: 1") == 2
    assert download.count("haloCells: 1") == 2
    assert "let dy = Float(0.11714935)" in download
    assert "downloadCamsGlobalArea" not in cams_download
    assert "cams-global-atmospheric-composition-forecasts" not in cams_download
    assert "getCamsGlobalAreaApiName" not in cams_domain
    assert "downloadCamsGlobal(application:" in cams_download
    assert "CamsRegionalDownload" in cams_download
    assert "data.shift180LongitudeAndFlipLatitude(nt: 1, ny: sourceNy, nx: sourceNx)" in cams_download
    assert "data = data.sliceGrid(" in cams_download
    grid_source = cams_domain.split("var grid: any Gridable", 1)[1]
    cams_global_grid = grid_source.split("case .cams_global:", 1)[1].split("case .cams_global_greenhouse_gases:", 1)[0]
    assert "WeatherForecastServerSourceConfig.regularGridSlice" in cams_global_grid
    assert "let base = RegularGrid(nx: 900, ny: 451, latMin: -90, lonMin: -180, dx: 0.4, dy: 0.4)" in cams_global_grid
    assert "RegularGrid(nx: slice.nx, ny: slice.ny" not in cams_global_grid
    assert "RegionalRegularGrid(base: base" in cams_global_grid


def test_openmeteo_raw_download_is_the_default_runtime_data_mode():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")
    singapore_env = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    assert "CACHE_SIZE=10GB" in singapore_env
    assert "download-gfs gfs013" in script
    assert "download-gfs gfs025" in script


def test_cams_downloaders_keep_ftp_and_ads_isolated_and_request_every_hour():
    ftp_download = read_vendor("Sources/App/Cams/CamsDownload.swift")
    ads_download = read_vendor("Sources/App/Cams/CamsDownloadAds.swift")
    configure = read_vendor("Sources/App/configure.swift")

    assert "func downloadCamsGlobalArea" not in ftp_download
    assert "CamsGlobalAreaQuery" not in ftp_download
    assert "downloadCdsApi(" not in ftp_download
    assert "readCamsGlobalArea" not in ftp_download
    assert "ADS" not in ftp_download
    assert "CDS" not in ftp_download
    assert "cdskey" not in ftp_download.lower()

    assert "struct DownloadCamsAdsCommand" in ads_download
    assert "func downloadCamsGlobalArea" in ads_download
    assert "CamsGlobalAreaQuery" in ads_download
    assert "downloadCdsApi(" in ads_download
    assert "readCamsGlobalArea" in ads_download
    assert "WEATHER_CAMS_FTP" not in ads_download
    assert "ftpuser" not in ads_download.lower()
    assert "ftppassword" not in ads_download.lower()
    assert 'app.asyncCommands.use(DownloadCamsCommand(), as: "download-cams")' in configure
    assert 'app.asyncCommands.use(DownloadCamsAdsCommand(), as: "download-cams-ads")' in configure
    assert "hour % 3" not in ftp_download
    assert "hour % 3" not in ads_download


def test_cams_greenhouse_gases_helper_belongs_to_ads_command_after_split():
    greenhouse = read_vendor("Sources/App/Cams/CamsGreenhouseGases.swift")

    assert "extension DownloadCamsAdsCommand" in greenhouse
    assert "extension DownloadCamsCommand" not in greenhouse


def test_cams_global_uses_ftp_ecpds_credentials_only():
    download = read_vendor("Sources/App/Cams/CamsDownload.swift")
    global_case = download.split("case .cams_global:", 1)[1].split("case .cams_europe:", 1)[0]

    assert 'WeatherForecastServerSourceConfig.string("WEATHER_CAMS_FTP_USER"' in global_case
    assert 'WeatherForecastServerSourceConfig.string("WEATHER_CAMS_FTP_PASSWORD"' in global_case
    assert "downloadCamsGlobal(" in global_case
    assert "cdskey" not in global_case.lower()
    assert "downloadCamsGlobalArea(" not in global_case
    assert "Both WEATHER_CAMS_FTP_USER and WEATHER_CAMS_FTP_PASSWORD are required" in global_case


def test_china_aqi_hourly_uses_current_hour_concentrations_without_rolling_windows():
    reader = read_vendor("Sources/App/Cams/CamsReader.swift")
    china_cases = reader.split("case .ch_aqi:", 1)[1].split("case .is_day:", 1)[0]

    assert ".slidingAverageDroppingFirstDt" not in china_cases
    assert "time.with(start:" not in china_cases
    assert "dropFirst" not in china_cases
    assert "24 * 3600" not in china_cases
    assert "8 * 3600" not in china_cases
    assert "o3_8h_mean" not in china_cases
