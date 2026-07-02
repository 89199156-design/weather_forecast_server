from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_root_dockerfile_builds_unmodified_vendored_openmeteo():
    dockerfile = ROOT / "docker" / "openmeteo-engine.Dockerfile"
    source = dockerfile.read_text(encoding="utf-8")

    assert "FROM ghcr.io/open-meteo/docker-container-build:latest AS build" in source
    assert "COPY vendor/openmeteo-sdk /build/openmeteo-sdk" not in source
    assert "COPY vendor/open-meteo/Package.swift /build/open-meteo/Package.swift" in source
    assert "COPY vendor/open-meteo/Package.*" not in source
    assert "COPY vendor/open-meteo /build/open-meteo" in source
    assert "WORKDIR /build/open-meteo" in source
    assert "ENABLE_PARQUET=TRUE swift package resolve" in source
    assert "COPY vendor/open-meteo/Package.resolved /build/open-meteo/Package.resolved" in source
    assert "rm -f Package.resolved" not in source
    assert "ENABLE_PARQUET=TRUE MARCH_SKYLAKE=TRUE swift build -c release" in source
    assert "apt-get install -y --no-install-recommends unzip" in source
    assert "COPY --from=build /usr/lib/x86_64-linux-gnu/libarrow*.so*" in source
    assert "COPY --from=build /usr/lib/x86_64-linux-gnu/libparquet*.so*" in source
    assert "RUN ldconfig" in source
    assert 'ENTRYPOINT ["./openmeteo-api"]' in source


def test_build_script_uses_root_context_and_new_repository_paths_only():
    script = (ROOT / "scripts" / "build_openmeteo_image.sh").read_text(encoding="utf-8")

    assert "docker/openmeteo-engine.Dockerfile" in script
    assert 'CONTEXT_DIR="$REPO_ROOT"' in script
    assert "weather_server_gfs" not in script
    assert "satellite" not in script.lower()


def test_singapore_internal_http_deploy_script_is_removed():
    assert not (ROOT / "scripts" / "deploy_singapore_candidate.sh").exists()


def test_legacy_bin_product_builders_are_removed():
    removed_paths = [
        ROOT / "scripts" / "build_openmeteo_point_package.py",
        ROOT / "scripts" / "build_openmeteo_pressure_profile_package.py",
        ROOT / "scripts" / "render_gfs_layers_from_point_package.py",
        ROOT / "tests" / "test_openmeteo_point_package.py",
        ROOT / "tests" / "test_openmeteo_pressure_profile_package.py",
    ]
    for path in removed_paths:
        assert not path.exists()

    active_paths = [
        ROOT / "README.md",
        ROOT / "scripts" / "build_server_openmeteo_layers.sh",
        ROOT / "scripts" / "build_openmeteo_gfs_layers.sh",
        ROOT / "scripts" / "build_openmeteo_cams_layers.sh",
        ROOT / "scripts" / "run_openmeteo_production_cycle.sh",
        ROOT / "scripts" / "run_gfs_production_cycle.sh",
        ROOT / "scripts" / "run_cams_production_cycle.sh",
        ROOT / "scripts" / "run_cams_ftp_production_cycle.sh",
        ROOT / "scripts" / "run_cams_ads_production_cycle.sh",
    ]
    forbidden = (
        "point_weather.bin",
        "pressure_profile.bin",
        "point_package",
        "pressure_profile_package",
        "openmeteo_points",
        "render_gfs_layers_from_point_package",
    )
    for path in active_paths:
        text = path.read_text(encoding="utf-8")
        for value in forbidden:
            assert value not in text


def test_runtime_data_download_covers_openmeteo_gfs_mixer_and_cams_global():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert "download-gfs gfs013" in script
    assert "download-gfs gfs025" in script
    assert "download_gfs025_upper_level_variable" in script
    assert "--only-variables" in script
    assert "--upper-level" not in script
    assert "download-cams cams_global" in script
    assert "CAMS_VARIABLES=" in script
    assert "--only-variables \"$CAMS_VARIABLES\"" in script
    assert "CAMS_FTP_USER=" in script
    assert "CAMS_FTP_PASSWORD=" in script
    assert "carbon_monoxide,nitrogen_dioxide,ozone,sulphur_dioxide" in script
    assert "WEATHER_CAMS_ADS" not in script
    assert "WEATHER_CAMS_CDS" not in script
    assert "read_cdsapi_key" not in script
    assert "--cdskey" not in script
    assert "--ftpuser" not in script
    assert "--ftppassword" not in script
    assert "set -a" in script
    assert "source_env_file" in script
    assert 'source_env_file "$ENV_FILE"' in script
    assert "weather_server_gfs" not in script
    assert "satellite" not in script.lower()


def test_runtime_data_download_cleans_only_temporary_download_workdirs_before_rebuild():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert "cleanup_download_work_dirs" in script
    for path in (
        '"$DATA_DIR/download-ncep_gfs013"',
        '"$DATA_DIR/download-ncep_gfs025"',
        '"$DATA_DIR/download-cams_global"',
    ):
        assert path in script
    for path in (
        '"$DATA_DIR/ncep_gfs013"',
        '"$DATA_DIR/ncep_gfs025"',
        '"$DATA_DIR/cams_global"',
        '"$DATA_DIR/data_run/ncep_gfs013"',
        '"$DATA_DIR/data_run/ncep_gfs025"',
    ):
        assert path not in script


def test_runtime_data_download_sources_env_before_runtime_defaults():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert script.index('source_env_file "$ENV_FILE"') < script.index("GFS_MAX_FORECAST_HOUR=")
    assert script.index('source_env_file "$ENV_FILE"') < script.index("CAMS_CONCURRENT=")


def test_runtime_data_download_chunks_gfs025_upper_levels_and_can_resume():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")
    upper_function = script.split("download_gfs025_upper_level_variable()", 1)[1].split(
        "require_dem_source", 1
    )[0]

    assert "GFS_UPPER_LEVEL_CHUNK_SIZE" in script
    assert "GFS_UPPER_LEVEL_CONCURRENT" in script
    assert "WEATHER_GFS_UPPER_LEVEL_DOWNLOAD_CONCURRENT" in script
    assert "GFS_UPPER_LEVEL_PGRB2_LEVELS" in script
    assert "1000,975,950,925,900,850,800,750,700,650,600,550,500,400,300,200" in script
    assert "level_is_in_csv" in script
    assert "primary_levels" in script
    assert "secondary_levels" in script
    assert "upper_level_only_variable_chunks" in script
    assert "while IFS= read -r only_variables" in script
    assert "download_gfs025_upper_level_variable" in script
    assert "WEATHER_SKIP_GFS013_DOWNLOAD" not in script
    assert "WEATHER_SKIP_GFS025_SURFACE_DOWNLOAD" not in script
    assert "WEATHER_SKIP_GFS025_UPPER_LEVEL_DOWNLOAD" not in script
    assert "WEATHER_SKIP_CAMS_DOWNLOAD" not in script
    assert "is_truthy" in script
    assert '--concurrent "$GFS_UPPER_LEVEL_CONCURRENT"' in upper_function
    assert '--concurrent "$GFS_CONCURRENT"' not in upper_function


def test_singapore_config_uses_bounded_pressure_level_product_contract():
    config = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    assert "WEATHER_GFS_UPPER_LEVELS=1000,975,950,925,900,850,800,750,700,650,600,550,500,400,300,200" in config
    assert (
        "WEATHER_GFS_UPPER_LEVEL_VARIABLES="
        "temperature,wind_u_component,wind_v_component,geopotential_height,cloud_cover,relative_humidity,vertical_velocity"
    ) in config
    assert "specific_humidity" not in config


def test_singapore_config_enables_temporary_openmeteo_http_cache():
    config = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert "WEATHER_OPENMETEO_HTTP_CACHE_ENABLED=true" in config
    assert "WEATHER_OPENMETEO_HTTP_CACHE_DIR=/app/data/http_cache" in config
    assert "WEATHER_OPENMETEO_HTTP_CACHE_CLEANUP=true" in config
    assert "HTTP_CACHE=" in script
    assert "host_http_cache_dir" in script
    assert 'rm -rf "$CACHE_DIR_HOST"' in script


def test_runtime_data_download_can_pin_domain_runs_without_engine_fork():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert "GFS013_RUN" in script
    assert "GFS025_RUN" in script
    assert "CAMS_RUN" in script
    assert "WEATHER_GFS013_RUN" in script
    assert "WEATHER_GFS025_RUN" in script
    assert "WEATHER_CAMS_RUN" in script
    assert 'append_run_arg "$GFS013_RUN"' in script
    assert 'append_run_arg "$GFS025_RUN"' in script
    assert 'append_run_arg "$CAMS_RUN"' in script
    assert "--run" in script


def test_runtime_data_download_defaults_to_raw_local_om_generation():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")
    singapore_env = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    assert "WEATHER_GFS_FILTER" not in singapore_env
    assert "WEATHER_CAMS_AREA_DOWNLOAD" not in singapore_env
    assert "WEATHER_CAMS_FTP_USER=" in singapore_env
    assert "WEATHER_CAMS_FTP_PASSWORD=" in singapore_env
    assert (
        "WEATHER_CAMS_VARIABLES="
        "pm2_5,pm10,aerosol_optical_depth,dust,carbon_monoxide,nitrogen_dioxide,ozone,sulphur_dioxide"
    ) in singapore_env
    assert "WEATHER_CAMS_ADS" not in singapore_env
    assert "WEATHER_CAMS_CDS" not in singapore_env
    assert "DATA_RUN_DIRECTORY=/app/data/data_run/" in singapore_env
    assert "CACHE_SIZE=10GB" in singapore_env
    assert "download-gfs gfs013" in script
    assert "download-gfs gfs025" in script


def test_runtime_data_download_uses_cams_ftp_ecpds_only():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")
    common = (ROOT / "scripts" / "openmeteo_runtime_common.sh").read_text(encoding="utf-8")

    assert "CAMS_FTP_USER=" in script
    assert "CAMS_FTP_PASSWORD=" in script
    assert "download_cams_ftp()" not in script
    assert "download_cams_ads()" not in script
    assert "CAMS_SOURCE" not in script
    assert "WEATHER_CAMS_SOURCE" not in script
    assert "ADS" not in script
    assert "CDS" not in script
    assert "cdsapi" not in script.lower()
    assert "ADS" not in common
    assert "CDS" not in common
    assert "cdsapi" not in common.lower()
    assert "--ftpuser" not in script
    assert "--ftppassword" not in script


def test_optional_cams_ads_cds_download_is_separate_from_ftp_ecpds():
    script_path = ROOT / "scripts" / "download_openmeteo_cams_ads_data.sh"
    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")
    ftp_script = (ROOT / "scripts" / "download_openmeteo_cams_data.sh").read_text(encoding="utf-8")

    assert "download-cams-ads cams_global" in script
    assert "--cdskey \"$CAMS_ADS_KEY\"" in script
    assert "read_cdsapi_key" in script
    assert "WEATHER_CAMS_FTP" not in script
    assert "CAMS_FTP" not in script
    assert "download-cams-ads" not in ftp_script
    assert "--cdskey" not in ftp_script
    assert "WEATHER_CAMS_ADS" not in ftp_script
    assert "WEATHER_CAMS_CDS" not in ftp_script


def test_singapore_config_keeps_cams_credentials_empty_for_private_override():
    config = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    assert "WEATHER_CAMS_SOURCE" not in config
    assert "WEATHER_CAMS_ADS" not in config
    assert "WEATHER_CAMS_CDS" not in config
    assert "WEATHER_CAMS_FTP_USER=" in config
    assert "WEATHER_CAMS_FTP_PASSWORD=" in config
    assert "config/singapore.private.env" in config
    assert "WEATHER_CAMS_AREA_DOWNLOAD" not in config


def test_runtime_data_download_filters_empty_env_values_before_docker_run():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert "SANITIZED_ENV_FILE" in script
    assert "mktemp" in script
    assert "cleanup_sanitized_env" in script
    assert "--env-file \"$SANITIZED_ENV_FILE\"" in script
    assert "--env-file \"$ENV_FILE\"" not in script
    assert "env | sort | awk -F=" in script
    assert "$1 ~ /^WEATHER_/" in script
    assert 'DATA_RUN_DIRECTORY' in script
    assert 'CACHE_SIZE' in script
    assert '$2 != ""' in script


def test_runtime_data_download_requires_dem_source_for_openmeteo_parity():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert "preseed_dem_region_static_files" in script
    assert "WEATHER_DEM_PRESEED_ENABLED" in script
    assert "WEATHER_DEM_PRESEED_BASE_URL" in script
    assert "WEATHER_DEM_PRESEED_CONCURRENT" in script
    assert "WEATHER_REGION_BOTTOM_LAT" in script
    assert "WEATHER_REGION_TOP_LAT" in script
    assert 'lat_${lat}.om' in script
    assert "compgen -G" not in script
    assert "require_dem_source" in script
    assert "copernicus_dem90/static/lat_*.om" in script
    assert "WEATHER_REQUIRE_DEM_SOURCE" in script


def test_runtime_data_download_preserves_explicit_environment_over_config_file():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert "capture_weather_env_overrides" in script
    assert "restore_weather_env_overrides" in script
    assert "WEATHER_ENV_OVERRIDES" in script
    capture_call = script.index("\ncapture_weather_env_overrides\n")
    source_call = script.index('source_env_file "$ENV_FILE"')
    restore_call = script.index("\nrestore_weather_env_overrides\n")
    assert capture_call < source_call < restore_call
    assert restore_call < script.index("GFS_MAX_FORECAST_HOUR=")


def test_openmeteo_downloader_only_changes_transport_and_region_grid():
    source = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDownload.swift").read_text(
        encoding="utf-8"
    )
    domain = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDomain.swift").read_text(
        encoding="utf-8"
    )
    cams_download = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Cams" / "CamsDownload.swift").read_text(
        encoding="utf-8"
    )
    cams_domain = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Cams" / "CamsDomain.swift").read_text(
        encoding="utf-8"
    )
    curl = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Helper" / "Download" / "Curl.swift").read_text(
        encoding="utf-8"
    )

    assert "cams-global-atmospheric-composition-forecasts" not in cams_download
    assert "downloadCamsGlobalArea" not in cams_download
    assert "getCamsGlobalAreaApiName" not in cams_domain
    assert "let base = RegularGrid(nx: 900, ny: 451, latMin: -90, lonMin: -180, dx: 0.4, dy: 0.4)" in cams_domain
    assert "return RegionalRegularGrid(base: base, x0: slice.x0, y0: slice.y0, nx: slice.nx, ny: slice.ny)" in cams_domain
    assert "downloadCamsGlobal(application:" in cams_download
    assert "CamsRegionalDownload" in cams_download
    assert "data = data.sliceGrid(" in cams_download
    assert "filter_gfs_0p25.pl" not in source
    assert "filter_gfs_0p25b.pl" not in source
    assert "filter_gfs_sflux.pl" not in source
    assert "downloadFilteredIndexedGrib" not in source
    assert "GfsRegionalDownload" in source
    assert "decodeRegional" in source
    assert "regularGridSlice" in domain
    assert "WEATHER_REGION_LEFT_LON" in domain
    assert "GfsController" not in source
    assert "weather_code" not in source
    assert "WeatherForecastServer" not in curl


def test_cams_global_download_is_ftp_ecpds_only():
    cams_download = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Cams" / "CamsDownload.swift").read_text(
        encoding="utf-8"
    )
    cams_domain = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Cams" / "CamsDomain.swift").read_text(
        encoding="utf-8"
    )

    global_case = cams_download.split("case .cams_global:", 1)[1].split("case .cams_europe:", 1)[0]
    assert "WEATHER_CAMS_FTP_USER" in global_case
    assert "WEATHER_CAMS_FTP_PASSWORD" in global_case
    assert "downloadCamsGlobal(" in global_case
    assert "cdskey" not in global_case.lower()
    assert "downloadCamsGlobalArea" not in cams_download
    assert "CamsGlobalAreaQuery" not in cams_download
    assert "readCamsGlobalArea" not in cams_download
    assert "getCamsGlobalAreaApiName" not in cams_domain


def test_layer_scripts_are_documented_as_openmeteo_engine_backed():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "build_openmeteo_layers.py").read_text(encoding="utf-8")
    validate_script = (ROOT / "scripts" / "validate_openmeteo_layers.py").read_text(encoding="utf-8")
    configure = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "configure.swift").read_text(encoding="utf-8")

    assert "scripts/build_openmeteo_layers.py" in readme
    assert "scripts/build_openmeteo_point_package.py" not in readme
    assert "scripts/render_gfs_layers_from_point_package.py" not in readme
    assert "scripts/build_server_openmeteo_layers.sh" in readme
    assert "scripts/validate_openmeteo_layers.py" in readme
    assert "Open-Meteo engine" in readme
    assert "import requests" not in build_script
    assert "requests.get" not in build_script
    assert "/v1/forecast" not in build_script
    assert "/v1/air-quality" not in build_script
    assert "127.0.0.1:18080" not in build_script
    assert 'app.asyncCommands.use(LayerGridExportCommand(), as: "export-layer-grid")' in configure
    assert "gfs_raw_download_core" not in build_script
    assert "gfs_raw_download_core" not in validate_script
    assert "satellite" not in build_script.lower()
    assert "satellite" not in validate_script.lower()


def test_server_layer_flow_builds_gfs_and_cams_products():
    script_path = ROOT / "scripts" / "build_server_openmeteo_layers.sh"
    assert script_path.exists()

    script = script_path.read_text(encoding="utf-8")

    assert "scripts/build_openmeteo_gfs_layers.sh" in script
    assert "scripts/build_openmeteo_cams_layers.sh" in script
    assert "data/public" in script
    assert "point_package" not in script
    assert "pressure_profile_package" not in script
    assert "openmeteo_points" not in script
    assert "WEATHER_OPENMETEO_GFS_API_URL" not in script
    assert "WEATHER_OPENMETEO_CAMS_API_URL" not in script
    assert "date -u -d" not in script
    assert "http://127.0.0.1:18080" not in script
    assert "/v1/forecast" not in script
    assert "/v1/air-quality" not in script
    assert "http://127.0.0.1:18084" not in script
    assert "scripts/build_openmeteo_point_package.py" not in script
    assert "scripts/build_openmeteo_pressure_profile_package.py" not in script
    assert "scripts/render_gfs_layers_from_point_package.py" not in script


def test_production_cycle_downloads_runtime_before_layer_build():
    script_path = ROOT / "scripts" / "run_openmeteo_production_cycle.sh"
    assert script_path.exists()

    script = script_path.read_text(encoding="utf-8")

    assert "scripts/download_openmeteo_runtime_data.sh" in script
    assert "scripts/build_server_openmeteo_layers.sh" in script
    assert "scripts/deploy_singapore_candidate.sh" not in script
    assert "restart local Open-Meteo API" not in script
    assert script.index("scripts/download_openmeteo_runtime_data.sh") < script.index("scripts/build_server_openmeteo_layers.sh")
    assert "download runtime data start=" in script
    assert "download runtime data end=" in script
    assert "build layer products start=" in script
    assert "build layer products end=" in script
    assert "flock -n" in script


def test_gfs_probe_cycle_uses_official_indices_before_gfs_only_production():
    probe = (ROOT / "scripts" / "probe_gfs_official_run.py").read_text(encoding="utf-8")
    cycle = (ROOT / "scripts" / "run_gfs_probe_and_cycle.sh").read_text(encoding="utf-8")
    production = (ROOT / "scripts" / "run_gfs_production_cycle.sh").read_text(encoding="utf-8")
    download = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")

    assert "nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod" in probe
    assert "sfluxgrbf{fff}.grib2.idx" in probe
    assert "pgrb2.0p25.f{fff}.idx" in probe
    assert "pgrb2b.0p25.f{fff}.idx" in probe
    assert "datetime.now(UTC)" in probe
    assert "scripts/probe_gfs_official_run.py" in cycle
    assert "CYCLE_LOCK_FILE" in cycle
    assert "GFS production cycle already running, skip probe." in cycle
    assert "scripts/run_gfs_production_cycle.sh" in cycle
    assert "scripts/download_openmeteo_gfs_data.sh" in production
    assert "scripts/build_openmeteo_gfs_layers.sh" in production
    assert "run_to_utc_layer_start" in production
    assert 'export WEATHER_OPENMETEO_GFS_RUN="$layer_start_hour"' in production
    assert 'export WEATHER_OPENMETEO_LAYER_FRAME_COUNT="121"' in production
    assert "unset WEATHER_OPENMETEO_LAYER_END_HOUR" in production
    assert "restart local Open-Meteo API" not in production
    assert "scripts/deploy_singapore_candidate.sh" not in production
    assert "download runtime data run=$RUN start=" in production
    assert "download runtime data run=$RUN end=" in production
    assert "build GFS layer products start=" in production
    assert "build GFS layer products end=" in production
    assert "download-cams" not in download
    assert "date -u" in cycle
    assert "CST" not in cycle


def test_cams_scheduled_cycle_uses_utc_twice_daily_target_logic():
    scheduled = (ROOT / "scripts" / "run_cams_scheduled_cycle.sh").read_text(encoding="utf-8")
    probe = (ROOT / "scripts" / "probe_cams_ftp_run.py").read_text(encoding="utf-8")
    production = (ROOT / "scripts" / "run_cams_production_cycle.sh").read_text(encoding="utf-8")
    download = (ROOT / "scripts" / "download_openmeteo_cams_data.sh").read_text(encoding="utf-8")

    assert "scripts/probe_cams_ftp_run.py" in scheduled
    assert "WEATHER_CAMS_SOURCE" not in scheduled
    assert "ftp|ecpds|ftp_ecpds)" not in scheduled
    assert "ads|cds|ads_cds)" not in scheduled
    assert "CAMS production cycle already running, skip probe." in scheduled
    assert "datetime.now(timezone.utc)" in scheduled
    assert "now.hour >= 22" in scheduled
    assert "now.hour >= 10" in scheduled
    assert "scripts/run_cams_production_cycle.sh" in scheduled
    assert "aux.ecmwf.int/ecpds/data/file/{directory}" in probe
    assert "CAMS_GLOBAL_ADDITIONAL" in probe
    assert "z_cams_c_ecmf_" in probe
    assert "Authorization" in probe
    assert "READY" in probe
    assert "NOT_READY" in probe
    assert "forecast_hour % 3" not in probe
    assert "hour % 3" not in probe
    assert "scripts/download_openmeteo_cams_data.sh" in production
    assert "scripts/build_openmeteo_cams_layers.sh" in production
    assert "run_to_utc_layer_start" in production
    assert "WEATHER_CAMS_SOURCE" not in production
    assert "download_openmeteo_cams_ads_data.sh" not in scheduled
    assert "download_openmeteo_cams_ads_data.sh" not in production
    assert 'export WEATHER_OPENMETEO_LAYER_FRAME_COUNT="121"' in production
    assert "unset WEATHER_OPENMETEO_LAYER_END_HOUR" in production
    assert "restart local Open-Meteo API" not in production
    assert "scripts/deploy_singapore_candidate.sh" not in production
    assert "download runtime data run=$RUN start=" in production
    assert "download runtime data run=$RUN end=" in production
    assert "build CAMS layer products start=" in production
    assert "build CAMS layer products end=" in production
    assert "download-gfs" not in download
    assert "date -u" in scheduled
    assert "CST" not in scheduled


def test_split_layer_builders_publish_only_their_product():
    gfs = (ROOT / "scripts" / "build_openmeteo_gfs_layers.sh").read_text(encoding="utf-8")
    cams = (ROOT / "scripts" / "build_openmeteo_cams_layers.sh").read_text(encoding="utf-8")

    assert 'LAYER_FRAME_COUNT="${WEATHER_OPENMETEO_LAYER_FRAME_COUNT:-121}"' in gfs
    assert 'LAYER_FRAME_COUNT="${WEATHER_OPENMETEO_LAYER_FRAME_COUNT:-121}"' in cams
    assert "normalize_run_hour" in gfs
    assert "date -u -d" not in gfs
    assert "date -u -d" not in cams
    assert "--scope gfs" in gfs
    assert "--scope cams" not in gfs
    assert "export-layer-grid" in gfs
    assert "http://127.0.0.1:18080" not in gfs
    assert "/v1/forecast" not in gfs
    assert "gfs013_surface" in gfs
    assert "cams_global" not in gfs
    assert "--scope cams" in cams
    assert "--scope gfs" not in cams
    assert "export-layer-grid" in cams
    assert "http://127.0.0.1:18080" not in cams
    assert "/v1/air-quality" not in cams
    assert "cams_global" in cams
    assert "gfs013_surface" not in cams
    assert "date -u" in gfs
    assert "date -u" in cams
