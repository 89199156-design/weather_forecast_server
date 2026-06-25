from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_root_dockerfile_builds_vendored_openmeteo_with_local_sdk():
    dockerfile = ROOT / "docker" / "openmeteo-engine.Dockerfile"
    source = dockerfile.read_text(encoding="utf-8")

    assert "FROM ghcr.io/open-meteo/docker-container-build:latest AS build" in source
    assert "COPY vendor/openmeteo-sdk /build/openmeteo-sdk" in source
    assert "COPY vendor/open-meteo/Package.swift /build/open-meteo/Package.swift" in source
    assert "COPY vendor/open-meteo/Package.*" not in source
    assert "COPY vendor/open-meteo /build/open-meteo" in source
    assert "WORKDIR /build/open-meteo" in source
    assert "ENABLE_PARQUET=TRUE swift package resolve" in source
    assert "rm -f Package.resolved" in source
    assert "open-meteo/sdk.git" not in source
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


def test_singapore_deploy_makes_data_directory_writable_by_openmeteo_user():
    script = (ROOT / "scripts" / "deploy_singapore_candidate.sh").read_text(encoding="utf-8")

    assert "WEATHER_OPENMETEO_UID" in script
    assert "WEATHER_OPENMETEO_GID" in script
    assert "chown" in script
    assert "$DATA_DIR" in script


def test_runtime_data_download_covers_openmeteo_gfs_mixer_and_cams_global():
    script = (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").read_text(encoding="utf-8")

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

    assert "GFS_UPPER_LEVEL_CHUNK_SIZE" in script
    assert "upper_level_only_variable_chunks" in script
    assert "while IFS= read -r only_variables" in script
    assert "download_gfs025_upper_level_variable" in script
    assert "WEATHER_SKIP_GFS013_DOWNLOAD" in script
    assert "WEATHER_SKIP_GFS025_SURFACE_DOWNLOAD" in script
    assert "is_truthy" in script


def test_layer_scripts_are_documented_as_openmeteo_api_backed():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "build_openmeteo_layers.py").read_text(encoding="utf-8")
    validate_script = (ROOT / "scripts" / "validate_openmeteo_layers.py").read_text(encoding="utf-8")

    assert "scripts/build_openmeteo_layers.py" in readme
    assert "scripts/validate_openmeteo_layers.py" in readme
    assert "Open-Meteo API" in readme
    assert "gfs_raw_download_core" not in build_script
    assert "gfs_raw_download_core" not in validate_script
    assert "satellite" not in build_script.lower()
    assert "satellite" not in validate_script.lower()
