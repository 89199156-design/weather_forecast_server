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


def test_singapore_deploy_example_uses_new_path_and_openmeteo_container():
    script = (ROOT / "scripts" / "deploy_singapore_candidate.sh").read_text(encoding="utf-8")

    assert "/opt/1panel/apps/weather_forecast_server" in script
    assert "weather_server_gfs" not in script
    assert "weather-forecast-openmeteo" in script
    assert "satellite" not in script.lower()


def test_fixed_snapshot_reference_script_runs_isolated_openmeteo_reference():
    script = (ROOT / "scripts" / "run_fixed_snapshot_reference.sh").read_text(encoding="utf-8")

    assert "weather-forecast-openmeteo-reference" in script
    assert "REMOTE_DATA_DIRECTORY" in script
    assert "WEATHER_DEM_REMOTE_DATA_DIRECTORY" not in script
    assert "CACHE_SIZE" in script
    assert "CACHE_META_SIZE" in script
    assert "127.0.0.1:${REFERENCE_PORT}:8080" in script
    assert "serve --env production --hostname 0.0.0.0 --port 8080" in script
    assert "weather_server_gfs" not in script
    assert "satellite" not in script.lower()


def test_singapore_deploy_makes_data_directory_writable_by_openmeteo_user():
    script = (ROOT / "scripts" / "deploy_singapore_candidate.sh").read_text(encoding="utf-8")

    assert "WEATHER_OPENMETEO_UID" in script
    assert "WEATHER_OPENMETEO_GID" in script
    assert "chown" in script
    assert "$DATA_DIR" in script


def test_singapore_deploy_filters_empty_env_values_before_docker_run():
    script = (ROOT / "scripts" / "deploy_singapore_candidate.sh").read_text(encoding="utf-8")

    assert "SANITIZED_ENV_FILE" in script
    assert "mktemp" in script
    assert "cleanup_sanitized_env" in script
    assert "--env-file \"$SANITIZED_ENV_FILE\"" in script
    assert "--env-file \"$ENV_FILE\"" not in script
    assert "env | sort | awk -F=" in script
    assert "$1 ~ /^WEATHER_/" in script
    assert 'REMOTE_DATA_DIRECTORY' in script
    assert 'CACHE_SIZE' in script
    assert '$2 != ""' in script


def test_singapore_deploy_requires_dem_source_for_openmeteo_parity():
    script = (ROOT / "scripts" / "deploy_singapore_candidate.sh").read_text(encoding="utf-8")

    assert "require_dem_source" in script
    assert "REMOTE_DATA_DIRECTORY" in script
    assert "copernicus_dem90/static/lat_*.om" in script
    assert "WEATHER_REQUIRE_DEM_SOURCE" in script


def test_runtime_data_download_covers_openmeteo_gfs_mixer_and_cams_global():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert "GFS_DOWNLOAD_MODE" in script
    assert "sync_openmeteo_database ncep_gfs013" in script
    assert "sync_openmeteo_database ncep_gfs025" in script
    assert "WEATHER_OPENMETEO_SYNC_BASE_URL" in script
    assert "download-gfs gfs013" in script
    assert "download-gfs gfs025" in script
    assert "download_gfs025_upper_level_variable" in script
    assert "--only-variables" in script
    assert "--upper-level" not in script
    assert "download-cams cams_global" in script
    assert "WEATHER_CAMS_FTP_USER" in script
    assert "WEATHER_CAMS_FTP_PASSWORD" in script
    assert "set -a" in script
    assert 'source "$ENV_FILE"' in script
    assert "weather_server_gfs" not in script
    assert "satellite" not in script.lower()


def test_runtime_data_download_sources_env_before_runtime_defaults():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert script.index('source "$ENV_FILE"') < script.index("GFS_MAX_FORECAST_HOUR=")
    assert script.index('source "$ENV_FILE"') < script.index("CAMS_CONCURRENT=")


def test_runtime_data_download_chunks_gfs025_upper_levels_and_can_resume():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")
    upper_function = script.split("download_gfs025_upper_level_variable()", 1)[1].split(
        "# gfs_global", 1
    )[0]

    assert "GFS_UPPER_LEVEL_CHUNK_SIZE" in script
    assert "GFS_UPPER_LEVEL_CONCURRENT" in script
    assert "WEATHER_GFS_UPPER_LEVEL_DOWNLOAD_CONCURRENT" in script
    assert "GFS_UPPER_LEVEL_PGRB2_LEVELS" in script
    assert "925,950,975,1000" in script
    assert "level_is_in_csv" in script
    assert "primary_levels" in script
    assert "secondary_levels" in script
    assert "upper_level_only_variable_chunks" in script
    assert "while IFS= read -r only_variables" in script
    assert "download_gfs025_upper_level_variable" in script
    assert "WEATHER_SKIP_GFS013_DOWNLOAD" in script
    assert "WEATHER_SKIP_GFS025_SURFACE_DOWNLOAD" in script
    assert "is_truthy" in script
    assert '--concurrent "$GFS_UPPER_LEVEL_CONCURRENT"' in upper_function
    assert '--concurrent "$GFS_CONCURRENT"' not in upper_function


def test_runtime_data_download_can_pin_domain_runs_without_engine_fork():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert "GFS013_RUN" in script
    assert "GFS025_RUN" in script
    assert "WEATHER_GFS013_RUN" in script
    assert "WEATHER_GFS025_RUN" in script
    assert 'append_run_arg "$GFS013_RUN"' in script
    assert 'append_run_arg "$GFS025_RUN"' in script
    assert "--run" in script


def test_runtime_data_download_sync_mode_uses_processed_openmeteo_database_for_parity():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")
    singapore_env = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    assert "WEATHER_GFS_DOWNLOAD_MODE=sync" in singapore_env
    assert "WEATHER_GFS_FILTER_DOWNLOAD" not in singapore_env
    assert "WEATHER_OPENMETEO_SYNC_BASE_URL" in singapore_env
    assert "REMOTE_DATA_DIRECTORY=" in singapore_env
    assert "CACHE_SIZE=10GB" in singapore_env
    assert "NOAA raw/filter conversion does not exactly match" in singapore_env
    assert "sync)" in script
    assert "raw)" in script
    assert 'run_openmeteo sync "$models" "$variables"' in script
    assert '$(append_sync_server_arg)' in script
    assert "--past-days \"$OPENMETEO_SYNC_PAST_DAYS\"" in script
    assert "--concurrent \"$OPENMETEO_SYNC_CONCURRENT\"" in script
    assert "GFS013_SYNC_VARIABLES" in script
    assert "GFS025_SURFACE_SYNC_VARIABLES" in script
    assert "gfs025_pressure_sync_variables" in script


def test_runtime_data_download_filters_empty_env_values_before_docker_run():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert "SANITIZED_ENV_FILE" in script
    assert "mktemp" in script
    assert "cleanup_sanitized_env" in script
    assert "--env-file \"$SANITIZED_ENV_FILE\"" in script
    assert "--env-file \"$ENV_FILE\"" not in script
    assert "env | sort | awk -F=" in script
    assert "$1 ~ /^WEATHER_/" in script
    assert 'REMOTE_DATA_DIRECTORY' in script
    assert 'CACHE_SIZE' in script
    assert '$2 != ""' in script


def test_runtime_data_download_requires_dem_source_for_openmeteo_parity():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert "require_dem_source" in script
    assert "REMOTE_DATA_DIRECTORY" in script
    assert "copernicus_dem90/static/lat_*.om" in script
    assert "WEATHER_REQUIRE_DEM_SOURCE" in script


def test_runtime_data_download_preserves_explicit_environment_over_config_file():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

    assert "capture_weather_env_overrides" in script
    assert "restore_weather_env_overrides" in script
    assert "WEATHER_ENV_OVERRIDES" in script
    capture_call = script.index("\ncapture_weather_env_overrides\n")
    source_call = script.index('source "$ENV_FILE"')
    restore_call = script.index("\nrestore_weather_env_overrides\n")
    assert capture_call < source_call < restore_call
    assert restore_call < script.index("GFS_MAX_FORECAST_HOUR=")


def test_gfs_downloader_is_not_forked_for_noaa_transport_or_filtering():
    source = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDownload.swift").read_text(
        encoding="utf-8"
    )
    curl = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Helper" / "Download" / "Curl.swift").read_text(
        encoding="utf-8"
    )

    assert "gfsNoaaDownloadHeaders" not in source
    assert "client: application.http1Client" not in source
    assert "WEATHER_GFS_FILTER_0P25B_URL" not in source
    assert "filter_gfs_0p25b.pl" not in source
    assert "WeatherForecastServer" not in curl


def test_layer_scripts_are_documented_as_openmeteo_api_backed():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "build_openmeteo_layers.py").read_text(encoding="utf-8")
    validate_script = (ROOT / "scripts" / "validate_openmeteo_layers.py").read_text(encoding="utf-8")

    assert "scripts/build_openmeteo_layers.py" in readme
    assert "scripts/build_server_openmeteo_layers.sh" in readme
    assert "scripts/validate_openmeteo_layers.py" in readme
    assert "Open-Meteo API" in readme
    assert "gfs_raw_download_core" not in build_script
    assert "gfs_raw_download_core" not in validate_script
    assert "satellite" not in build_script.lower()
    assert "satellite" not in validate_script.lower()


def test_server_layer_flow_builds_gfs_and_cams_products():
    script_path = ROOT / "scripts" / "build_server_openmeteo_layers.sh"
    assert script_path.exists()

    script = script_path.read_text(encoding="utf-8")

    assert "WEATHER_OPENMETEO_LAYER_FRAME_COUNT" in script
    assert "gfs013_surface" in script
    assert "cams_global" in script
    assert '--scope gfs' in script
    assert '--scope cams' in script
    assert "WEATHER_OPENMETEO_GFS_API_URL" in script
    assert "WEATHER_OPENMETEO_CAMS_API_URL" in script
    assert "scripts/build_openmeteo_layers.py" in script
