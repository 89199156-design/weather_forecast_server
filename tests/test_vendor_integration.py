from pathlib import Path
import hashlib
import re


ROOT = Path(__file__).resolve().parents[1]


def read_vendor(path: str) -> str:
    return (ROOT / "vendor" / "open-meteo" / path).read_text(encoding="utf-8")


def read_vendor_normalized_bytes(path: str) -> bytes:
    normalized = (
        (ROOT / "vendor" / "open-meteo" / path)
        .read_bytes()
        .replace(b"\r\n", b"\n")
        .replace(b"\r", b"\n")
    )
    return b"\n".join(line.rstrip() for line in normalized.split(b"\n"))


def test_openmeteo_package_uses_upstream_sdk_dependency():
    package = read_vendor("Package.swift")

    assert 'url: "https://github.com/open-meteo/sdk.git", from: "1.26.0"' in package
    assert '.package(path: "../openmeteo-sdk")' not in package
    assert '.product(name: "OpenMeteoSdk", package: "sdk")' in package
    assert '.product(name: "OpenMeteoSdk", package: "openmeteo-sdk")' not in package


def test_core_api_output_files_match_single_upstream_engine_baseline():
    expected_sha256 = {
        "Sources/App/Helper/WeatherCode.swift": "2555314d6353ed9763f96a5a145dc217249351ff8fc56e228e5277eed7d85928",
        "Sources/App/Gfs/GfsController.swift": "756d68a69195850cc2e5c628d7b9bd47e2fa6aa2fc469e5bba857d2d521ed95e",
        "Sources/App/Controllers/ForecastapiController.swift": "cdd4e64b0d7c7f6081cc57aafe7771d6408d5d5df791c9c49d58248b099e0361",
        "Sources/App/Controllers/VariableHourly.swift": "19c92be6728d8318e5ce4fdf0368c10baa4361bb42507ae9a65de05fc35458ec",
        "Sources/App/Helper/Reader/DerivedMapping.swift": "9d4cc081f53bfd3b84e528ba16114bfd229d204be3ffd1afd070edac2fa74576",
        "Sources/App/Helper/Meteorology.swift": "d0a5bfdde7009ad37deb934bcd74ea46eb92d27e2d22dd017a7b8b2281f164a4",
        "Sources/App/Helper/NumberExtensions.swift": "c0e85e7ed4c5b355924e8f6435425d18f3ce51bacb1197cf4dee92db3039542e",
        "Sources/App/Helper/Writer/JsonWriter.swift": "81419406fb880b36890a42d94eb8acebe44d8e72de72e40435d5aea2887b249e",
        "Sources/App/Helper/Writer/CsvWriter.swift": "82b6e4bef05ef062e2bca728140bc756fcc64a4e5bea5fdb8bb0adeed5b5819f",
        "Sources/App/Helper/Writer/ForecastApiResult.swift": "352f445e78319eab1112b3a5faba4cf75707685ce30e5d3817a4e8967f5f04d9",
        "Sources/App/Helper/FlatBufferWriter/FlatBuffersWriter.swift": "ac880de07d1b7267185f65f753aa836d5ba65d755bc7f7487b06b75b4ecb9fb6",
        "Sources/App/Helper/FlatBufferWriter/FlatBuffers+WeatherApi.swift": "4ae93cd562c4c87d0bf4149e67bde536d4e74bb31fd677b1b99a62cf360a78d8",
        "Sources/App/Helper/Vapor/ApiKeyManager.swift": "06c1fed7bf121322d3b1e496a48387d305fa9c99dfc8bea5228732f0856092ab",
        "Sources/App/Helper/Vapor/ConcurrencyGroupLimiter.swift": "7e8ddaf64ed30ad921635eb884dd0bb31488adb645fe595d31cd0368e42a3206",
        "Sources/App/Helper/Vapor/RateLimiter.swift": "97c979b8c03b44f6512037aa9789601e049e235b7d406e017ca43609af188346",
        "Sources/App/Dem/DemController.swift": "3e3e42fed7f63163c061a9416eede8ae36761f402b8a872d53cfa2d3fe5fdeb1",
        "Sources/App/Domains/RegularGrid.swift": "213bbf2906ac62fcd4ea5bbc6a101adaea08173a0f1b8885837ad424cc635b4d",
        "Sources/App/Chmi/ChmiDomain.swift": "3f9eb2a56934c1f7709dbbb319868fdd9f02825ea13be37d1464209e001805b6",
        "Sources/App/Chmi/ChmiVariable.swift": "ce15cfc42896ebf0ee78e9e45853dbc8a65797d67e9c791a5f09bf763647fee3",
    }

    for path, expected in expected_sha256.items():
        actual = hashlib.sha256(read_vendor_normalized_bytes(path)).hexdigest()
        assert actual == expected, f"{path} differs from Open-Meteo 4efb9c49"


def test_gfs_hourly_deriver_keeps_upstream_derived_surface_logic():
    deriver = read_vendor("Sources/App/Controllers/VariableHourly.swift")
    vpd_case = deriver.split("case .vapour_pressure_deficit, .vapor_pressure_deficit:", 1)[1].split("case .evapotranspiration:", 1)[0]

    assert 'let rh = self.getDeriverMap(variable: .relativehumidity_2m)' in vpd_case
    assert ".two(.raw(temperature), .mapped(rh))" in vpd_case

    upstream_required_snippets = (
        'case .weather_code, .weathercode:',
        '.windSpeed(u: Reader.variableFromString("wind_u_component_100m"), v: Reader.variableFromString("wind_v_component_100m"), levelFrom: 100, levelTo: 80)',
        '.windDirection(u: Reader.variableFromString("wind_u_component_100m"), v: Reader.variableFromString("wind_v_component_100m"))',
        '.windSpeed(u: Reader.variableFromString("wind_u_component_200m"), v: Reader.variableFromString("wind_v_component_200m"), levelFrom: 200, levelTo: 180)',
    )
    for snippet in upstream_required_snippets:
        assert snippet in deriver
    assert 'convectiveInhibition: Reader.variableFromString("convective_inhibition")' in deriver
    assert 'boundaryLayerHeight: Reader.variableFromString("boundary_layer_height")' in deriver


def test_openmeteo_chmi_domain_registration_matches_selected_upstream_case():
    registry = read_vendor("Sources/App/Helper/DomainRegistry.swift")

    assert "case chmi_aladin_cz_1km" in registry
    assert "case .chmi_aladin_cz_1km:\n            return ChmiDomain.aladin_cz_1km" in registry


def test_upstream_record_uses_one_openmeteo_engine_baseline():
    upstream = (ROOT / "UPSTREAM.md").read_text(encoding="utf-8")

    assert "`4efb9c49fb4a3718ed385fb22580d2e0fc56bdb2`" in upstream
    assert "GFS JSON/CSV writer behavior baseline" not in upstream
    assert "GFS weather-code API behavior baseline" not in upstream
    assert "Active local shared engine baseline" not in upstream
    assert "`036c1d940f2dd5af48f899c2d8162d00d12d3c49`" not in upstream
    assert "`98a3e0f00bf13633c5511a6c7788462088bfe752`" not in upstream


def test_vendored_openmeteo_only_has_required_region_patches():
    domain = read_vendor("Sources/App/Gfs/GfsDomain.swift")
    download = read_vendor("Sources/App/Gfs/GfsDownload.swift")
    cams_domain = read_vendor("Sources/App/Cams/CamsDomain.swift")
    cams_download = read_vendor("Sources/App/Cams/CamsDownload.swift")
    curl = read_vendor("Sources/App/Helper/Download/Curl.swift")

    assert "WeatherForecastServerSourceConfig" in domain
    assert "regularGridSlice" in domain
    assert "RegionalRegularGrid" in domain
    assert "WEATHER_REGION_LEFT_LON" in domain
    assert "GfsRegionalDownload" in download
    assert "decodeRegional" in download

    combined = "\n".join((domain, download, curl))
    for token in ("WEATHER_GFS_FILTER", "GfsController", "weather_code", "calculateThunderstormProbability"):
        assert token not in combined

    assert "downloadIndexedGrib" in download
    assert "RegularGrid(nx: 1440, ny: 721, latMin: -90, lonMin: -180, dx: 0.25, dy: 0.25)" in domain
    assert "return RegionalRegularGrid(base: base" in domain

    assert "downloadCamsGlobalArea" not in cams_download
    assert "cams-global-atmospheric-composition-forecasts" not in cams_download
    assert "getCamsGlobalAreaApiName" not in cams_domain
    assert "downloadCamsGlobal(application:" in cams_download
    assert "domain.regionalDownloadSlice" in cams_download
    assert "data.sliceGrid(" in cams_download
    assert "WeatherForecastServerSourceConfig" in cams_domain


def test_openmeteo_raw_download_is_the_default_runtime_data_mode():
    script = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")
    singapore_env = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    assert "CACHE_SIZE=10GB" in singapore_env
    assert "download-gfs gfs013" in script
    assert "download-gfs gfs025" in script


def test_cams_downloaders_remain_upstream_without_project_ads_split():
    ftp_download = read_vendor("Sources/App/Cams/CamsDownload.swift")
    configure = read_vendor("Sources/App/configure.swift")

    assert "func downloadCamsGlobalArea" not in ftp_download
    assert "CamsGlobalAreaQuery" not in ftp_download
    assert "readCamsGlobalArea" not in ftp_download
    assert "func downloadCamsGlobalArea" not in ftp_download

    assert not (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Cams" / "CamsDownloadAds.swift").exists()
    assert 'app.asyncCommands.use(DownloadCamsCommand(), as: "download-cams")' in configure
    assert 'DownloadCamsAdsCommand' not in configure


def test_cams_greenhouse_gases_helper_remains_upstream_download_command_extension():
    greenhouse = read_vendor("Sources/App/Cams/CamsGreenhouseGases.swift")

    assert "extension DownloadCamsCommand" in greenhouse
    assert "extension DownloadCamsAdsCommand" not in greenhouse


def test_cams_greenhouse_gases_are_not_region_sliced_inside_vendor():
    domain = read_vendor("Sources/App/Cams/CamsDomain.swift")
    greenhouse = read_vendor("Sources/App/Cams/CamsGreenhouseGases.swift")

    grid_source = domain.split("var grid: any Gridable", 1)[1]
    greenhouse_grid = grid_source.split("case .cams_global_greenhouse_gases:", 1)[1].split("case .cams_europe:", 1)[0]
    assert "RegularGrid(nx: 3600, ny: 1801, latMin: -90, lonMin: -180, dx: 0.1, dy: 0.1)" in greenhouse_grid
    assert "RegionalRegularGrid(base: base" not in greenhouse_grid
    assert "regionalSlice" not in greenhouse
    assert "data.sliceGrid(" not in greenhouse


def test_cams_global_uses_upstream_ftp_options_not_project_env_config():
    download = read_vendor("Sources/App/Cams/CamsDownload.swift")
    global_case = download.split("case .cams_global:", 1)[1].split("case .cams_europe:", 1)[0]

    assert "signature.ftpuser" in global_case
    assert "signature.ftppassword" in global_case
    assert "WEATHER_CAMS_FTP_USER" not in global_case
    assert "WEATHER_CAMS_FTP_PASSWORD" not in global_case
    assert "downloadCamsGlobal(" in global_case
    assert "cdskey" not in global_case.lower()
    assert "downloadCamsGlobalArea(" not in global_case


def test_china_aqi_is_not_patched_into_vendored_cams_reader():
    reader = read_vendor("Sources/App/Cams/CamsReader.swift")

    assert "case .ch_aqi:" not in reader
    assert "ch_iaqi_" not in reader


def test_china_aqi_formula_is_not_patched_into_vendored_air_quality():
    air_quality = read_vendor("Sources/App/Helper/AirQuality.swift")
    reader = read_vendor("Sources/App/Cams/CamsReader.swift")

    assert "enum ChinaAirQuality" not in air_quality
    assert "ChinaAirQuality" not in reader
