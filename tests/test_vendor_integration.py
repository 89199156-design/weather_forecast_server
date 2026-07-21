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
    assert "`6059e2bd7e009b765caadd6a619002af3fd9ee21`" in upstream
    assert "GFS JSON/CSV writer behavior baseline" not in upstream
    assert "GFS weather-code API behavior baseline" not in upstream
    assert "Active local shared engine baseline" not in upstream
    assert "`036c1d940f2dd5af48f899c2d8162d00d12d3c49`" not in upstream
    assert "`98a3e0f00bf13633c5511a6c7788462088bfe752`" not in upstream


def test_vendored_openmeteo_only_has_required_region_patches():
    domain = read_vendor("Sources/App/Gfs/GfsDomain.swift")
    download = read_vendor("Sources/App/Gfs/GfsDownload.swift")
    regional_download = read_vendor("Sources/App/Gfs/GfsNomadsRegionalDownload.swift")
    cams_domain = read_vendor("Sources/App/Cams/CamsDomain.swift")
    cams_download = read_vendor("Sources/App/Cams/CamsDownload.swift")
    cams_greenhouse = read_vendor("Sources/App/Cams/CamsGreenhouseGases.swift")
    curl = read_vendor("Sources/App/Helper/Download/Curl.swift")

    assert "WeatherForecastServerSourceConfig" in domain
    assert "regularGridSlice" in domain
    assert "RegionalRegularGrid" in domain
    assert "WEATHER_REGION_LEFT_LON" in domain
    assert "GfsRegionalDownload" in download
    assert "decodeRegional" in download
    assert "multiplyAdd(domain: domain, dtSeconds: dtSeconds)" in download
    assert "let dtSeconds = previousHour == 0 ? domain.dtSeconds" in download

    combined = "\n".join((domain, download, curl))
    for token in ("WEATHER_GFS_FILTER", "GfsController", "weather_code", "calculateThunderstormProbability"):
        assert token not in combined

    assert "downloadIndexedGrib" in download
    assert "GfsNomadsRegionalDownload.inventoryUrl" in regional_download
    assert "https://noaa-gfs-bdp-pds.s3.amazonaws.com/" in regional_download
    assert '"WEATHER_NOMADS_REQUEST_DELAY_SECONDS"' in regional_download
    assert "fallback: 10" in regional_download
    assert "minimumInterval: max(10, delay)" in regional_download
    assert "cachedFilterResponse" in regional_download
    assert "filter_gfs_sflux.pl" in regional_download
    assert "filter_gfs_0p25.pl" in regional_download
    assert "filter_gfs_0p25b.pl" in regional_download
    assert 'case "0-0.1 m below ground"' in regional_download
    assert 'case "0.4-1 m below ground"' in regional_download
    assert 'case "80 m above ground"' in regional_download
    assert 'case "0C isotherm"' in regional_download
    assert "RegularGrid(nx: 1440, ny: 721, latMin: -90, lonMin: -180, dx: 0.25, dy: 0.25)" in domain
    assert "return RegionalRegularGrid(base: base" in domain

    assert "downloadCamsGlobalArea" not in cams_download
    assert "cams-global-atmospheric-composition-forecasts" not in cams_download
    assert "getCamsGlobalAreaApiName" not in cams_domain
    assert "func downloadCamsGlobal(" in cams_download
    assert "domain.regionalDownloadSlice" in cams_download
    assert "data.sliceGrid(" in cams_download
    assert "WeatherForecastServerSourceConfig" in cams_domain
    assert 'dataset: "cams-global-greenhouse-gas-forecasts"' in cams_greenhouse
    assert "getCamsGlobalGreenhouseGasesMeta" in cams_greenhouse
    assert "area: nil" in cams_greenhouse
    assert "domain.regionalDownloadSlice" in cams_greenhouse
    assert "data.sliceGrid(" in cams_greenhouse


def test_cams_greenhouse_ads_download_keeps_upstream_full_grid_values_and_crops_locally():
    cams_download = read_vendor("Sources/App/Cams/CamsDownload.swift")
    cams_greenhouse = read_vendor("Sources/App/Cams/CamsGreenhouseGases.swift")

    assert "let area: [Double]?" in cams_download
    assert "area: nil" in cams_greenhouse
    assert "let regionalSlice = domain.regionalDownloadSlice" in cams_greenhouse
    assert "let sourceNx = regionalSlice?.fullNx" in cams_greenhouse
    assert "let sourceNy = regionalSlice?.fullNy" in cams_greenhouse
    assert "nx: sourceNx" in cams_greenhouse
    assert "ny: sourceNy" in cams_greenhouse
    assert "shift180LongitudeAndFlipLatitudeIfRequired: true" in cams_greenhouse
    assert "data = data.sliceGrid(" in cams_greenhouse
    assert "sourceNx: sourceNx" in cams_greenhouse
    assert "data.multiplyAdd(multiply: scaling, add: 0)" in cams_greenhouse


def test_cams_full_run_export_uses_official_hourly_interpolation_axis():
    writer = read_vendor("Sources/App/Helper/Writer/GenericVariableHandle.swift")

    helper = writer.split("func fullRunTimestamps", 1)[1].split(
        "struct GenericVariableHandle", 1
    )[0]
    full_run = writer.split("static func generateFullRunData", 1)[1].split(
        "private static func convertConcurrent", 1
    )[0]

    assert "domainRegistry == .cams_global" in helper
    assert "last.add(dtSeconds)" in helper
    assert "let requiresInterpolation = sourceTimes.count != time.count" in full_run
    assert "data3d.interpolateInplace(" in full_run
    assert "type: variable.interpolation" in full_run
    assert "time: timeRange" in full_run


def test_cds_ads_queue_state_is_fail_closed_and_post_is_never_retried():
    cds = read_vendor("Sources/App/Helper/Download/Curl+CDS.swift")

    assert "case uncertainSubmission" in cds
    assert "fileprivate enum CdsApiResumePhase" in cds
    assert "case submitting" in cds
    assert "case submitted" in cds
    assert "phase: .submitting" in cds
    assert "job: nil" in cds
    assert "throw CdsApiError.uncertainSubmission" in cds
    assert "case submissionRejected" in cds
    assert "case .invalidCombinationOfValues, .submissionRejected:" in cds
    assert "if (400..<500).contains(response.status.code)" in cds
    assert "throw CdsApiError.submissionRejected" in cds
    assert 'throw CdsApiError.invalidResponse(message: "Could not decode CDS job response")' in cds
    assert "phase: .submitted" in cds
    start = cds.split("fileprivate func startCdsApiJob", 1)[1].split(
        "fileprivate func waitForCdsJob", 1
    )[0]
    assert "client.execute(request" in start
    assert "executeRetry" not in start
    assert "error404WaitTime" in cds
    assert 'environment["WEATHER_CDS_POLL_INTERVAL_SECONDS"]' in cds
    assert 'environment["WEATHER_CDS_JOB_TIMEOUT_HOURS"]' in cds


def test_openmeteo_raw_download_is_the_default_runtime_data_mode():
    script = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")
    singapore_env = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    assert "CACHE_SIZE=10GB" in singapore_env
    assert "download-gfs gfs013" in script
    assert "download-gfs gfs025" in script


def test_cams_download_command_keeps_ecpds_global_and_official_greenhouse_paths():
    ftp_download = read_vendor("Sources/App/Cams/CamsDownload.swift")
    configure = read_vendor("Sources/App/configure.swift")

    assert "func downloadCamsGlobalArea" not in ftp_download
    assert "CamsGlobalAreaQuery" not in ftp_download
    assert "readCamsGlobalArea" not in ftp_download
    assert "func downloadCamsGlobalArea" not in ftp_download

    assert not (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Cams" / "CamsDownloadAds.swift").exists()
    assert (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Cams" / "CamsGreenhouseGases.swift").exists()
    assert 'app.asyncCommands.use(DownloadCamsCommand(), as: "download-cams")' in configure
    assert 'DownloadCamsAdsCommand' not in configure
    assert 'case .cams_global_greenhouse_gases:' in ftp_download
    assert "downloadCamsGlobalGreenhouseGases(" in ftp_download
    assert "Only cams_global and cams_global_greenhouse_gases are enabled" in ftp_download


def test_cams_global_accepts_environment_credentials_without_command_line_exposure():
    download = read_vendor("Sources/App/Cams/CamsDownload.swift")

    assert "signature.ftpuser" in download
    assert "signature.ftppassword" in download
    assert 'ProcessInfo.processInfo.environment["WEATHER_CAMS_FTP_USER"]' in download
    assert 'ProcessInfo.processInfo.environment["WEATHER_CAMS_FTP_PASSWORD"]' in download
    assert "downloadCamsGlobal(" in download
    assert 'ProcessInfo.processInfo.environment["WEATHER_CAMS_ADS_KEY"]' in download
    assert "downloadCamsGlobalArea(" not in download


def test_china_aqi_is_not_patched_into_vendored_cams_reader():
    reader = read_vendor("Sources/App/Cams/CamsReader.swift")

    assert "case .ch_aqi:" not in reader
    assert "ch_iaqi_" not in reader


def test_china_aqi_formula_is_not_patched_into_vendored_air_quality():
    air_quality = read_vendor("Sources/App/Helper/AirQuality.swift")
    reader = read_vendor("Sources/App/Cams/CamsReader.swift")

    assert "enum ChinaAirQuality" not in air_quality
    assert "ChinaAirQuality" not in reader
