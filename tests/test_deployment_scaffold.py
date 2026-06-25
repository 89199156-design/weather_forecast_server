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
