from pathlib import Path
import importlib.util
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def load_validation_batches_module():
    path = ROOT / "scripts" / "validate_openmeteo_official_50point_batches.py"
    spec = importlib.util.spec_from_file_location("validate_openmeteo_official_50point_batches", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_validation_candidates_can_use_remote_inventory_without_local_data(tmp_path):
    validator = load_validation_batches_module()
    variables = validator.candidate_variables(
        ROOT,
        "gfs",
        tmp_path / "missing-openmeteo-data",
        gfs_pressure_compare_levels_hpa={"850hPa"},
        actual_names_by_domain={
            "ncep_gfs013": {
                "temperature_2m",
                "relative_humidity_2m",
                "cloud_cover",
                "pressure_msl",
                "wind_u_component_10m",
                "wind_v_component_10m",
            },
            "ncep_gfs025": {
                "temperature_850hPa",
                "relative_humidity_850hPa",
                "wind_u_component_850hPa",
                "wind_v_component_850hPa",
            },
        },
    )

    assert "temperature_2m" in variables
    assert "wind_speed_10m" in variables
    assert "temperature_850hPa" in variables
    assert "relativehumidity_850hPa" in variables
    assert "wind_speed_850hPa" in variables


def test_direct_validation_exports_local_variables_once_per_batch(monkeypatch, tmp_path):
    validator = load_validation_batches_module()
    local_requests = []
    reference_requests = []

    def fake_local_hourlies(**kwargs):
        variables = [item for item in kwargs["params"]["hourly"].split(",") if item]
        local_requests.append(tuple(variables))
        hourly = {"time": ["2026-07-03T18:00"]}
        for variable in variables:
            hourly[variable] = [1.0]
        return [hourly]

    def fake_reference_hourlies(**kwargs):
        variables = [item for item in kwargs["params"]["hourly"].split(",") if item]
        reference_requests.append(tuple(variables))
        hourly = {"time": ["2026-07-03T18:00"]}
        for variable in variables:
            hourly[variable] = [1.0]
        return [hourly]

    monkeypatch.setattr(validator, "fetch_local_hourlies", fake_local_hourlies)
    monkeypatch.setattr(validator, "fetch_hourlies", fake_reference_hourlies)

    report = validator.validate_scope_batch(
        scope="gfs",
        batch_index=1,
        points=[{"latitude": 30.0, "longitude": 120.0}],
        variables=["temperature_2m", "relative_humidity_2m", "wind_speed_10m"],
        api_base_url="",
        local_openmeteo_mode="direct",
        data_dir=tmp_path,
        output_dir=tmp_path,
        openmeteo_image="image",
        openmeteo_tag="tag",
        direct_ssh_host="singapore",
        direct_remote_root="/srv/weather",
        reference_base_url="https://single-runs-api.open-meteo.com",
        reference_ssh_host="seoul",
        gfs_run="2026-07-03T18:00",
        gfs_reference_mode="single-run",
        start_hour="2026-07-03T18:00",
        end_hour="2026-07-03T18:00",
        frames=1,
        chunk_size=1,
        tolerance=0.001,
        timeout=10,
        retries=0,
        retry_delay=0,
        request_pause=0,
        gfs_model="gfs_global",
    )

    assert report["passed"] is True
    assert local_requests == [("temperature_2m", "relative_humidity_2m", "wind_speed_10m")]
    assert reference_requests == [("temperature_2m",), ("relative_humidity_2m",), ("wind_speed_10m",)]


def test_openmeteo_json_writer_uses_current_upstream_numeric_formatting():
    number_extensions = (
        ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Helper" / "NumberExtensions.swift"
    ).read_text(encoding="utf-8")
    json_writer = (
        ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Helper" / "Writer" / "JsonWriter.swift"
    ).read_text(encoding="utf-8")
    csv_writer = (
        ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Helper" / "Writer" / "CsvWriter.swift"
    ).read_text(encoding="utf-8")
    forecast_result = (
        ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Helper" / "Writer" / "ForecastApiResult.swift"
    ).read_text(encoding="utf-8")

    assert "func formatted(decimals: Int) -> String" in number_extensions
    assert ".formatted(decimals: e.unit.significantDigits)" in json_writer
    assert ".formatted(decimals: e.unit.significantDigits)" in csv_writer
    assert ".formatted(decimals: 2)" in forecast_result
    assert "String(format: format" not in json_writer
    assert "String(format: \"%." not in csv_writer


def test_legacy_combined_and_bin_product_builders_are_removed():
    removed_paths = [
        ROOT / "scripts" / "build_openmeteo_point_package.py",
        ROOT / "scripts" / "build_openmeteo_pressure_profile_package.py",
        ROOT / "scripts" / "render_gfs_layers_from_point_package.py",
        ROOT / "scripts" / "run_gfs_point_package.sh",
        ROOT / "scripts" / "run_gfs_profile_build.sh",
        ROOT / "scripts" / "download_openmeteo_runtime_data.sh",
        ROOT / "scripts" / "run_openmeteo_production_cycle.sh",
        ROOT / "scripts" / "build_server_openmeteo_layers.sh",
        ROOT / "tests" / "test_openmeteo_point_package.py",
        ROOT / "tests" / "test_openmeteo_pressure_profile_package.py",
    ]
    for path in removed_paths:
        assert not path.exists()

    active_paths = [
        ROOT / "README.md",
        ROOT / "scripts" / "build_openmeteo_gfs_layers.sh",
        ROOT / "scripts" / "build_openmeteo_cams_layers.sh",
        ROOT / "scripts" / "run_gfs_production_cycle.sh",
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


def test_split_downloaders_cover_openmeteo_domains_without_cross_source_coupling():
    gfs = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")
    ftp = (ROOT / "scripts" / "download_openmeteo_cams_data.sh").read_text(encoding="utf-8")
    ads = (ROOT / "scripts" / "download_openmeteo_cams_ads_data.sh").read_text(encoding="utf-8")

    assert "download-gfs gfs013" in gfs
    assert "download-gfs gfs025" in gfs
    assert "download_gfs025_upper_level_variables" in gfs
    assert "--only-variables" in gfs
    assert "--upper-level" not in gfs
    assert "download-cams" not in gfs
    assert "CAMS_" not in gfs

    assert "download-cams cams_global" in ftp
    assert "CAMS_VARIABLES=" in ftp
    assert "--only-variables \"$CAMS_VARIABLES\"" in ftp
    assert "CAMS_FTP_USER=" in ftp
    assert "CAMS_FTP_PASSWORD=" in ftp
    assert 'CAMS_CONCURRENT="${WEATHER_CAMS_FTP_DOWNLOAD_CONCURRENT:-8}"' in ftp
    assert "WEATHER_CAMS_DOWNLOAD_CONCURRENT" not in ftp
    assert "carbon_monoxide,nitrogen_dioxide,ozone,sulphur_dioxide" in ftp
    assert "download-gfs" not in ftp
    assert "WEATHER_CAMS_ADS" not in ftp
    assert "WEATHER_CAMS_CDS" not in ftp
    assert "read_cdsapi_key" not in ftp
    assert "--cdskey" not in ftp

    assert "download-cams-ads cams_global" in ads
    assert "--cdskey \"$CAMS_ADS_KEY\"" in ads
    assert "read_cdsapi_key" in ads
    assert 'CAMS_CONCURRENT="${WEATHER_CAMS_ADS_DOWNLOAD_CONCURRENT:-1}"' in ads
    assert "WEATHER_CAMS_DOWNLOAD_CONCURRENT" not in ads
    assert "download-gfs" not in ads
    assert "download-cams cams_global" not in ads
    assert "WEATHER_CAMS_FTP" not in ads
    assert "CAMS_FTP" not in ads


def test_split_downloaders_clean_only_their_temporary_workdirs_before_rebuild():
    gfs = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")
    ftp = (ROOT / "scripts" / "download_openmeteo_cams_data.sh").read_text(encoding="utf-8")
    ads = (ROOT / "scripts" / "download_openmeteo_cams_ads_data.sh").read_text(encoding="utf-8")

    assert "cleanup_download_work_dirs" in gfs
    for path in (
        '"$DATA_DIR/download-ncep_gfs013"',
        '"$DATA_DIR/download-ncep_gfs025"',
    ):
        assert path in gfs
    assert '"$DATA_DIR/download-cams_global"' not in gfs

    assert 'cleanup_download_work_dirs "$DATA_DIR/download-cams_global"' in ftp
    assert '"$DATA_DIR/download-cams_global"' in ads
    assert '"$DATA_DIR/download-cams_global_greenhouse_gases"' in ads
    for script in (ftp, ads):
        assert '"$DATA_DIR/download-ncep_gfs013"' not in script
        assert '"$DATA_DIR/download-ncep_gfs025"' not in script

    combined = "\n".join((gfs, ftp, ads))
    for path in (
        '"$DATA_DIR/ncep_gfs013"',
        '"$DATA_DIR/ncep_gfs025"',
        '"$DATA_DIR/cams_global"',
        '"$DATA_DIR/data_run/ncep_gfs013"',
        '"$DATA_DIR/data_run/ncep_gfs025"',
    ):
        assert path not in combined


def test_split_downloaders_source_env_before_runtime_defaults():
    scripts = [
        (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8"),
        (ROOT / "scripts" / "download_openmeteo_cams_data.sh").read_text(encoding="utf-8"),
        (ROOT / "scripts" / "download_openmeteo_cams_ads_data.sh").read_text(encoding="utf-8"),
    ]

    for script in scripts:
        assert script.index("load_weather_env") < script.index("openmeteo_set_runtime_defaults")


def test_runtime_data_download_packages_gfs025_upper_levels_as_one_variable_collection():
    script = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")
    common = (ROOT / "scripts" / "openmeteo_runtime_common.sh").read_text(encoding="utf-8")
    config = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")
    upper_function = script.split("download_gfs025_upper_level_variables()", 1)[1].split(
        "require_dem_source", 1
    )[0]

    assert "GFS_UPPER_LEVEL_CONCURRENT" in script
    assert "WEATHER_GFS_UPPER_LEVEL_DOWNLOAD_CONCURRENT" in script
    assert 'GFS_UPPER_LEVEL_CONCURRENT="${WEATHER_GFS_UPPER_LEVEL_DOWNLOAD_CONCURRENT:-4}"' in script
    assert "WEATHER_GFS_UPPER_LEVEL_DOWNLOAD_CONCURRENT=4" in config
    assert "GFS_UPPER_LEVEL_CHUNK_SIZE" not in script
    assert "WEATHER_GFS_UPPER_LEVEL_CHUNK_SIZE" not in config
    assert "GFS_UPPER_LEVEL_PGRB2_LEVELS" not in script
    assert "WEATHER_GFS_UPPER_LEVEL_PGRB2_LEVELS" not in config
    assert "1000,975,950,925,900,850,800,750,700,650,600,550,500,400,300,200" in script
    assert "gfs025_upper_level_only_variables" in script
    assert "join_by_comma" in script
    assert "level_is_in_csv" not in script
    assert "primary_levels" not in script
    assert "secondary_levels" not in script
    assert "upper_level_only_variable_chunks" not in script
    assert "while IFS= read -r only_variables" not in script
    assert "download_gfs025_upper_level_variables" in script
    assert "download_gfs025_upper_level_variable()" not in script
    assert 'for variable in "${variables[@]}"' not in upper_function
    assert "WEATHER_SKIP_GFS013_DOWNLOAD" not in script
    assert "WEATHER_SKIP_GFS025_SURFACE_DOWNLOAD" not in script
    assert "WEATHER_SKIP_GFS025_UPPER_LEVEL_DOWNLOAD" not in script
    assert "WEATHER_SKIP_CAMS_DOWNLOAD" not in script
    assert "is_truthy" in common
    assert 'GFS025_UPPER_LEVEL_ONLY_VARIABLES="$(gfs025_upper_level_only_variables)"' in upper_function
    assert '--only-variables "$GFS025_UPPER_LEVEL_ONLY_VARIABLES"' in upper_function
    assert '--concurrent "$GFS_UPPER_LEVEL_CONCURRENT"' in upper_function
    assert '--concurrent "$GFS_CONCURRENT"' not in upper_function
    assert script.count("run_openmeteo download-gfs gfs025") == 2


def test_gfs_upper_level_download_does_not_break_run_argument_splitting():
    script = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")
    upper_function = script.split("download_gfs025_upper_level_variables()", 1)[1].split(
        "require_dem_source", 1
    )[0]

    assert 'local IFS=","' not in upper_function
    assert 'read -ra variables <<< "$GFS_UPPER_LEVEL_VARIABLES"' in script
    assert '$(append_run_arg "$GFS_RUN")' in upper_function


def test_gfs_surface_download_uses_explicit_minimal_allowlists():
    script = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")
    config = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    assert "GFS013_SURFACE_VARIABLES=" in script
    assert "GFS025_SURFACE_VARIABLES=" in script
    assert "WEATHER_GFS013_SURFACE_VARIABLES=" in config
    assert "WEATHER_GFS025_SURFACE_VARIABLES=" in config

    gfs013_default = (
        "temperature_2m,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,"
        "pressure_msl,relative_humidity_2m,precipitation,wind_v_component_10m,"
        "wind_u_component_10m,snow_depth,showers,frozen_precipitation_percent,"
        "uv_index,boundary_layer_height,shortwave_radiation,latent_heat_flux"
    )
    gfs025_default = (
        "pressure_msl,categorical_freezing_rain,wind_gusts_10m,cape,lifted_index,"
        "convective_inhibition,visibility,latent_heat_flux"
    )
    assert gfs013_default in script
    assert gfs025_default in script
    assert f"WEATHER_GFS013_SURFACE_VARIABLES={gfs013_default}" in config
    assert f"WEATHER_GFS025_SURFACE_VARIABLES={gfs025_default}" in config

    for command_block in (
        script.split("run_openmeteo download-gfs gfs013", 1)[1].split("run_openmeteo download-gfs gfs025", 1)[0],
        script.split("run_openmeteo download-gfs gfs025", 1)[1].split('IFS="," read -ra upper_variables', 1)[0],
    ):
        assert "--only-variables" in command_block

    gfs013_block = script.split("run_openmeteo download-gfs gfs013", 1)[1].split("run_openmeteo download-gfs gfs025", 1)[0]
    gfs025_block = script.split("run_openmeteo download-gfs gfs025", 1)[1].split('IFS="," read -ra upper_variables', 1)[0]
    assert '--only-variables "$GFS013_SURFACE_VARIABLES"' in gfs013_block
    assert '--only-variables "$GFS025_SURFACE_VARIABLES"' in gfs025_block

    unused_surface_variables = (
        "temperature_80m",
        "temperature_100m",
        "wind_v_component_80m",
        "wind_u_component_80m",
        "wind_v_component_100m",
        "wind_u_component_100m",
        "surface_temperature",
        "soil_temperature_0_to_10cm",
        "soil_temperature_10_to_40cm",
        "soil_temperature_40_to_100cm",
        "soil_temperature_100_to_200cm",
        "soil_moisture_0_to_10cm",
        "soil_moisture_10_to_40cm",
        "soil_moisture_40_to_100cm",
        "soil_moisture_100_to_200cm",
        "sensible_heat_flux",
        "freezing_level_height",
        "diffuse_radiation",
        "uv_index_clear_sky",
        "total_column_integrated_water_vapour",
        "mass_density_8m",
    )
    configured_surface = f"{gfs013_default},{gfs025_default}"
    for variable in unused_surface_variables:
        assert variable not in configured_surface


def test_singapore_config_uses_bounded_pressure_level_product_contract():
    config = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

    assert "WEATHER_GFS_UPPER_LEVELS=1000,975,950,925,900,850,800,750,700,650,600,550,500,400,300,200" in config
    assert (
        "WEATHER_GFS_UPPER_LEVEL_VARIABLES="
        "temperature,wind_u_component,wind_v_component,geopotential_height,cloud_cover,relative_humidity,vertical_velocity"
    ) in config
    assert "WEATHER_GFS_UPPER_LEVEL_PGRB2_LEVELS" not in config
    assert "WEATHER_GFS_UPPER_LEVEL_CHUNK_SIZE" not in config
    assert "specific_humidity" not in config


def test_gfs_production_validates_pressure_level_directories_before_publish():
    script = (ROOT / "scripts" / "run_gfs_production_cycle.sh").read_text(encoding="utf-8")

    assert "GFS_UPPER_LEVELS=" in script
    assert "GFS_UPPER_LEVEL_VARIABLES=" in script
    assert "--required-gfs-pressure-domain ncep_gfs025" in script
    assert '--required-gfs-pressure-levels "$GFS_UPPER_LEVELS"' in script
    assert '--required-gfs-pressure-variables "$GFS_UPPER_LEVEL_VARIABLES"' in script
    assert script.index("--required-gfs-pressure-domain ncep_gfs025") < script.index("bash scripts/build_openmeteo_gfs_layers.sh")


def test_latest_run_validation_requires_configured_pressure_dirs(tmp_path):
    data_dir = tmp_path / "openmeteo"
    latest_dir = data_dir / "data_run" / "ncep_gfs025"
    latest_dir.mkdir(parents=True)
    (latest_dir / "latest.json").write_text(
        '{"reference_time":"2026-07-03T18:00:00Z","valid_times":["x","y"]}',
        encoding="utf-8",
    )

    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "validate_openmeteo_latest_run.py"),
        "--data-dir",
        str(data_dir),
        "--run",
        "2026070318",
        "--domains",
        "ncep_gfs025",
        "--min-frames",
        "2",
        "--required-gfs-pressure-domain",
        "ncep_gfs025",
        "--required-gfs-pressure-levels",
        "1000",
        "--required-gfs-pressure-variables",
        "temperature",
    ]
    missing = subprocess.run(cmd, text=True, capture_output=True, check=False)
    assert missing.returncode == 1
    assert "missing required variable directory temperature_1000hPa" in missing.stderr

    (data_dir / "ncep_gfs025" / "temperature_1000hPa").mkdir(parents=True)
    present = subprocess.run(cmd, text=True, capture_output=True, check=False)
    assert present.returncode == 0
    assert "OK 2026-07-03T18:00:00Z ncep_gfs025" in present.stdout


def test_singapore_config_enables_temporary_openmeteo_http_cache():
    config = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")
    common = (ROOT / "scripts" / "openmeteo_runtime_common.sh").read_text(encoding="utf-8")
    gfs_script = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")
    cams_script = (ROOT / "scripts" / "download_openmeteo_cams_data.sh").read_text(encoding="utf-8")
    cams_ads_script = (ROOT / "scripts" / "download_openmeteo_cams_ads_data.sh").read_text(encoding="utf-8")

    assert "WEATHER_OPENMETEO_HTTP_CACHE_ENABLED=true" in config
    assert "WEATHER_OPENMETEO_HTTP_CACHE_DIR=/app/data/http_cache" in config
    assert "WEATHER_OPENMETEO_HTTP_CACHE_CLEANUP=true" in config
    assert "HTTP_CACHE=" in common
    assert "host_http_cache_dir" in common
    assert 'rm -rf "$cache_dir_host"' in common

    for script in (gfs_script, cams_script, cams_ads_script):
        assert "cleanup_sensitive_artifacts()" in script
        assert "cleanup_openmeteo_http_cache" in script
        assert "trap cleanup_sensitive_artifacts EXIT" in script
        assert "trap cleanup_download_artifacts EXIT" not in script
        assert "cleanup_download_artifacts()" not in script
        trap_index = script.index("trap cleanup_sensitive_artifacts EXIT")
        start_cleanup_index = script.index("\ncleanup_openmeteo_http_cache\n", trap_index)
        first_download_index = script.index("run_openmeteo")
        assert trap_index < start_cleanup_index < first_download_index
        success_cleanup_index = script.rindex("\ncleanup_openmeteo_http_cache\n")
        assert first_download_index < success_cleanup_index

    expected_cache_dirs = {
        gfs_script: "/app/data/http_cache/gfs",
        cams_script: "/app/data/http_cache/cams_ftp",
        cams_ads_script: "/app/data/http_cache/cams_ads",
    }
    for script, cache_dir in expected_cache_dirs.items():
        cache_index = script.index(f'WEATHER_OPENMETEO_HTTP_CACHE_DIR="{cache_dir}"')
        http_cache_index = script.index(f'HTTP_CACHE="{cache_dir}"')
        defaults_index = script.index("openmeteo_set_runtime_defaults")
        assert cache_index < http_cache_index < defaults_index


def test_downloaders_clean_source_cache_only_at_start_and_after_success():
    scripts = {
        "gfs": (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8"),
        "cams_ftp": (ROOT / "scripts" / "download_openmeteo_cams_data.sh").read_text(encoding="utf-8"),
        "cams_ads": (ROOT / "scripts" / "download_openmeteo_cams_ads_data.sh").read_text(encoding="utf-8"),
    }

    for name, script in scripts.items():
        assert "trap cleanup_sensitive_artifacts EXIT" in script
        assert "trap cleanup_download_artifacts EXIT" not in script
        assert "cleanup_download_artifacts()" not in script
        cleanup_calls = [line for line in script.splitlines() if line.strip() == "cleanup_openmeteo_http_cache"]
        if name == "gfs":
            assert len(cleanup_calls) >= 4
        else:
            assert len(cleanup_calls) == 2
        first_run = script.index("run_openmeteo")
        last_run = script.rindex("run_openmeteo")
        first_cleanup = script.index("\ncleanup_openmeteo_http_cache\n")
        last_cleanup = script.rindex("\ncleanup_openmeteo_http_cache\n")
        assert first_cleanup < first_run
        assert last_run < last_cleanup
        if name != "gfs":
            assert "cleanup_openmeteo_http_cache" not in script[first_run:last_run]

    gfs = scripts["gfs"]
    assert gfs.count("cleanup_download_work_dirs \\") == 2
    assert gfs.count('"$DATA_DIR/download-ncep_gfs013"') == 3
    assert gfs.count('"$DATA_DIR/download-ncep_gfs025"') >= 4
    assert gfs.index("cleanup_download_work_dirs \\") < gfs.index("run_openmeteo download-gfs gfs013")
    assert gfs.rindex("run_openmeteo download-gfs gfs025") < gfs.rindex("cleanup_download_work_dirs \\")
    assert gfs.index("run_openmeteo download-gfs gfs013") < gfs.index('cleanup_download_work_dirs "$DATA_DIR/download-ncep_gfs013"')
    assert gfs.index("run_openmeteo download-gfs gfs025") < gfs.index('cleanup_download_work_dirs "$DATA_DIR/download-ncep_gfs025"')

    cams_ftp = scripts["cams_ftp"]
    assert cams_ftp.count('cleanup_download_work_dirs "$DATA_DIR/download-cams_global"') == 2
    assert cams_ftp.index('cleanup_download_work_dirs "$DATA_DIR/download-cams_global"') < cams_ftp.index("run_openmeteo")
    assert cams_ftp.rindex("run_openmeteo") < cams_ftp.rindex('cleanup_download_work_dirs "$DATA_DIR/download-cams_global"')

    cams_ads = scripts["cams_ads"]
    assert cams_ads.count('"$DATA_DIR/download-cams_global"') == 2
    assert cams_ads.count('"$DATA_DIR/download-cams_global_greenhouse_gases"') == 2
    assert cams_ads.index("cleanup_download_work_dirs \\") < cams_ads.index("run_openmeteo download-cams-ads cams_global")
    assert cams_ads.rindex("run_openmeteo download-cams-ads cams_global_greenhouse_gases") < cams_ads.rindex("cleanup_download_work_dirs \\")


def test_production_cycles_keep_runtime_products_until_safe_publish():
    gfs = (ROOT / "scripts" / "run_gfs_production_cycle.sh").read_text(encoding="utf-8")
    cams_ftp = (ROOT / "scripts" / "run_cams_ftp_production_cycle.sh").read_text(encoding="utf-8")
    cams_ads = (ROOT / "scripts" / "run_cams_ads_production_cycle.sh").read_text(encoding="utf-8")

    assert "cleanup_gfs_generated_products" not in gfs
    assert "prepare_gfs_staging_data_dir" in gfs
    assert 'export WEATHER_OPENMETEO_DATA_DIR="$GFS_STAGING_DATA_DIR"' in gfs
    assert "for domain in ncep_gfs013 ncep_gfs025; do" in gfs
    assert '"$ACTIVE_DATA_DIR/$domain"' in gfs
    assert '"$ACTIVE_DATA_DIR/data_run/$domain"' in gfs
    assert '"$GFS_STAGING_DATA_DIR/$domain"' in gfs
    assert '"$GFS_STAGING_DATA_DIR/data_run/$domain"' in gfs
    assert '"$DATA_DIR/cams_global"' not in gfs
    assert "cleanup_gfs_generated_products" not in cams_ftp
    assert "cleanup_gfs_generated_products" not in cams_ads

    for script in (cams_ftp, cams_ads):
        assert "cleanup_cams_generated_products" not in script
        assert 'rm -rf "$DATA_DIR/cams_global"' not in script
        assert 'rm -rf "$DATA_DIR/data_run/cams_global"' not in script
        assert '"$DATA_DIR/cams_global"' not in script
        assert '"$DATA_DIR/data_run/cams_global"' not in script
        assert "ncep_gfs013" not in script
        assert "ncep_gfs025" not in script
    assert '"$DATA_DIR/cams_global_greenhouse_gases"' not in cams_ftp
    assert '"$DATA_DIR/data_run/cams_global_greenhouse_gases"' not in cams_ftp
    assert 'rm -rf "$DATA_DIR/cams_global_greenhouse_gases"' not in cams_ads
    assert 'rm -rf "$DATA_DIR/data_run/cams_global_greenhouse_gases"' not in cams_ads
    assert '"$DATA_DIR/cams_global_greenhouse_gases"' not in cams_ads
    assert '"$DATA_DIR/data_run/cams_global_greenhouse_gases"' not in cams_ads

    assert gfs.index("prepare_gfs_staging_data_dir") < gfs.index("bash scripts/download_openmeteo_gfs_data.sh")
    assert gfs.index("bash scripts/build_openmeteo_gfs_layers.sh") < gfs.index("\n  publish_gfs_products\n")


def test_runtime_data_download_can_pin_source_runs_without_engine_fork():
    gfs = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")
    cams = (ROOT / "scripts" / "download_openmeteo_cams_data.sh").read_text(encoding="utf-8")
    ads = (ROOT / "scripts" / "download_openmeteo_cams_ads_data.sh").read_text(encoding="utf-8")
    production = (ROOT / "scripts" / "run_gfs_production_cycle.sh").read_text(encoding="utf-8")

    assert "GFS_RUN" in gfs
    assert "WEATHER_GFS_RUN" in gfs
    assert "GFS013_RUN" not in gfs
    assert "GFS025_RUN" not in gfs
    assert "WEATHER_GFS013_RUN" not in gfs
    assert "WEATHER_GFS025_RUN" not in gfs
    assert gfs.count('append_run_arg "$GFS_RUN"') == 3
    assert "WEATHER_GFS013_RUN" not in production
    assert "WEATHER_GFS025_RUN" not in production
    for script in (cams, ads):
        assert "CAMS_RUN" in script
        assert "WEATHER_CAMS_RUN" in script
        assert 'append_run_arg "$CAMS_RUN"' in script


def test_runtime_data_download_defaults_to_raw_local_om_generation():
    gfs = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")
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
    assert "download-gfs gfs013" in gfs
    assert "download-gfs gfs025" in gfs


def test_runtime_data_download_uses_cams_ftp_ecpds_only():
    script = (ROOT / "scripts" / "download_openmeteo_cams_data.sh").read_text(encoding="utf-8")
    common = (ROOT / "scripts" / "openmeteo_runtime_common.sh").read_text(encoding="utf-8")

    assert "CAMS_FTP_USER=" in script
    assert "CAMS_FTP_PASSWORD=" in script
    assert 'CAMS_CONCURRENT="${WEATHER_CAMS_FTP_DOWNLOAD_CONCURRENT:-8}"' in script
    assert "WEATHER_CAMS_DOWNLOAD_CONCURRENT" not in script
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
    assert "download-cams-ads cams_global_greenhouse_gases" in script
    assert "WEATHER_CAMS_GREENHOUSE_VARIABLES" in script
    assert "CAMS_GREENHOUSE_RUN=" in script
    assert 'append_run_arg "$CAMS_GREENHOUSE_RUN"' in script
    assert "carbon_monoxide" in script
    assert "--cdskey \"$CAMS_ADS_KEY\"" in script
    assert "read_cdsapi_key" in script
    assert 'CAMS_CONCURRENT="${WEATHER_CAMS_ADS_DOWNLOAD_CONCURRENT:-1}"' in script
    assert "WEATHER_CAMS_DOWNLOAD_CONCURRENT" not in script
    assert "WEATHER_CAMS_FTP" not in script
    assert "CAMS_FTP" not in script
    assert "download-cams-ads" not in ftp_script
    assert "cams_global_greenhouse_gases" not in ftp_script
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
    assert "WEATHER_CAMS_FTP_DOWNLOAD_CONCURRENT=8" in config
    assert "WEATHER_CAMS_DOWNLOAD_CONCURRENT" not in config
    assert "config/singapore.private.env" in config
    assert "WEATHER_CAMS_AREA_DOWNLOAD" not in config


def test_runtime_data_download_filters_empty_env_values_before_docker_run():
    script = (ROOT / "scripts" / "openmeteo_runtime_common.sh").read_text(encoding="utf-8")
    gfs = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")

    assert "SANITIZED_ENV_FILE" in script
    assert "mktemp" in script
    assert "cleanup_sensitive_artifacts" in gfs
    assert "trap cleanup_sensitive_artifacts EXIT" in gfs
    assert "trap cleanup_download_artifacts EXIT" not in gfs
    assert "--env-file \"$SANITIZED_ENV_FILE\"" in script
    assert "--env-file \"$ENV_FILE\"" not in script
    assert "env | sort | awk -F=" in script
    assert "$1 ~ /^WEATHER_/" in script
    assert 'DATA_RUN_DIRECTORY' in script
    assert 'CACHE_SIZE' in script
    assert '$2 != ""' in script


def test_runtime_data_download_requires_dem_source_for_openmeteo_parity():
    script = (ROOT / "scripts" / "openmeteo_runtime_common.sh").read_text(encoding="utf-8")

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
    script = (ROOT / "scripts" / "openmeteo_runtime_common.sh").read_text(encoding="utf-8")

    assert "capture_weather_env_overrides" in script
    assert "restore_weather_env_overrides" in script
    assert "WEATHER_ENV_OVERRIDES" in script
    load_weather_env = script.split("load_weather_env() {", 1)[1].split("\n}", 1)[0]
    capture_call = load_weather_env.index("capture_weather_env_overrides")
    source_call = load_weather_env.index('source_env_file "$ENV_FILE"')
    restore_call = load_weather_env.index("restore_weather_env_overrides")
    assert capture_call < source_call < restore_call


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
    assert "domain.regionalDownloadSlice" in cams_download
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


def test_cams_ftp_concurrent_option_drives_file_downloads():
    cams_download = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Cams" / "CamsDownload.swift").read_text(
        encoding="utf-8"
    )
    global_case = cams_download.split("case .cams_global:", 1)[1].split("default:", 1)[0]
    download_function = cams_download.split("func downloadCamsGlobal(", 1)[1].split("await curl.printStatistics()", 1)[0]

    assert "concurrent: signature.concurrent ?? 1" in global_case
    assert "func downloadCamsGlobal(" in cams_download
    assert "concurrent: Int" in download_function
    assert "jobs.foreachConcurrent(nConcurrent: max(1, concurrent))" in download_function
    assert "curl.download(url: job.remoteFile, toFile: job.tempNc" in download_function
    assert r'"\(domain.downloadDirectory)/temp.nc"' not in cams_download
    assert r'temp_\(hour.zeroPadded(len: 3))_\(meta.gribname).nc' in cams_download


def test_layer_scripts_are_documented_as_openmeteo_engine_backed():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "build_openmeteo_layers.py").read_text(encoding="utf-8")
    validate_script = (ROOT / "scripts" / "validate_openmeteo_layers.py").read_text(encoding="utf-8")
    configure = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "configure.swift").read_text(encoding="utf-8")
    export_command = (
        ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Commands" / "LayerGridExportCommand.swift"
    ).read_text(encoding="utf-8")

    assert "scripts/build_openmeteo_layers.py" in readme
    assert "scripts/build_openmeteo_point_package.py" not in readme
    assert "scripts/render_gfs_layers_from_point_package.py" not in readme
    assert "scripts/build_server_openmeteo_layers.sh" not in readme
    assert "scripts/build_openmeteo_gfs_layers.sh" in readme
    assert "scripts/build_openmeteo_cams_layers.sh" in readme
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
    assert "Dem90.read(lat: lat, lon: lon" in export_command
    assert "elevation: .nan" not in export_command


def test_point_export_command_exposes_openmeteo_reader_without_http():
    configure = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "configure.swift").read_text(
        encoding="utf-8"
    )
    command_path = ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Commands" / "PointForecastExportCommand.swift"
    command = command_path.read_text(encoding="utf-8")
    validator = (ROOT / "scripts" / "validate_openmeteo_official_50point_batches.py").read_text(
        encoding="utf-8"
    )

    assert 'app.asyncCommands.use(PointForecastExportCommand(), as: "export-point-forecast")' in configure
    assert "struct PointForecastExportCommand: AsyncCommand" in command
    assert "let points: [PointForecastExportPoint]" in command
    assert "ForecastVariable.load" in command
    assert "params.prepareCoordinates" in command
    assert "domain.getReaders(" in command
    assert "location.hourly(variables: hourlyVariables)" in command
    assert "readMixed(readers:" in command
    assert "/v1/forecast" not in command
    assert "/v1/air-quality" not in command
    assert "app.http" not in command

    assert "--local-openmeteo-mode" in validator
    assert "choices=(\"http\", \"direct\")" in validator
    assert "fetch_direct_hourlies" in validator
    assert "export-point-forecast" in validator


def test_point_export_command_supports_cams_derived_variables_without_weather_parser():
    command = (
        ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Commands" / "PointForecastExportCommand.swift"
    ).read_text(encoding="utf-8")
    variable_parser = command.split("let hourlyVariables: [ForecastVariable]", 1)[1].split("let outputURL", 1)[0]
    cams_export = command.split('if request.scope == "cams" {\n                let rawReaders', 1)[1].split(
        "let timeLocal",
        1,
    )[0]

    assert "CamsReader.MixingVar.load" in variable_parser
    assert "hourlyVariables = try ForecastVariable.load" in variable_parser
    assert "ForecastVariable.load" not in variable_parser.split('if request.scope == "cams" {', 1)[1].split(
        "} else {",
        1,
    )[0]
    assert "domains = [try MultiDomains.load(rawValue: request.model)]" in command
    assert "CamsQuery.Domain.load" not in command
    assert "GenericReader<CamsDomain, CamsVariable>" not in cams_export
    assert "domain.getReader(" in cams_export
    assert "readMixed(readers: rawReaders" in cams_export
    assert "location.hourly(variables: hourlyVariables)" not in cams_export
    assert "reader.get(mixed: variable" in command


def test_gfs_weather_code_keeps_upstream_thunderstorm_logic():
    weather_code = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Helper" / "WeatherCode.swift").read_text(
        encoding="utf-8"
    )
    gfs_controller = (ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsController.swift").read_text(
        encoding="utf-8"
    )
    derived_mapping = (
        ROOT / "vendor" / "open-meteo" / "Sources" / "App" / "Helper" / "Reader" / "DerivedMapping.swift"
    ).read_text(encoding="utf-8")

    assert "calculateThunderstormProbability" in weather_code
    assert "convectiveInhibition: Float?" in weather_code
    assert "pblHeight: Float?" in weather_code
    assert "latitude: Float" in weather_code
    assert "latitudeFactor" in weather_code
    assert "cinWeight" in weather_code
    assert "pblWeight" in weather_code
    assert "0.8 + (0.2 * (absLat / 30.0))" in weather_code
    assert "(cape - 300.0) / (maxCapeThreshold - 300.0)" in weather_code
    assert "let precipWeight: Float = 0.25" in weather_code
    assert "if thunderstroms > 90" in weather_code
    assert "return .thunderstormHeavy" in weather_code
    assert "if thunderstroms > 70" in weather_code
    assert "return .thunderstormStrong" in weather_code
    assert "if thunderstroms > 50" in weather_code
    assert "return .thunderstormSlightOrModerate" in weather_code
    assert "if thunderstroms > 85" not in weather_code
    assert "if thunderstroms > 60" not in weather_code
    assert "if cloudcover < 30.0" not in weather_code
    assert "cloudCoverFactor" not in weather_code

    weather_prefetch = gfs_controller.split("case .weather_code, .weathercode:", 1)[1].split(
        "case .is_day:", 1
    )[0]
    weather_get = gfs_controller.split("case .weather_code, .weathercode:", 2)[2].split(
        "case .is_day:", 1
    )[0]
    assert "raw: .surface(.convective_inhibition)" in weather_prefetch
    assert "raw: .surface(.boundary_layer_height)" in weather_prefetch
    assert "let convective_inhibition = try await get(raw: .surface(.convective_inhibition)" in weather_get
    assert "let boundary_layer_height = try await get(raw: .surface(.boundary_layer_height)" in weather_get
    assert "convectiveInhibition: convective_inhibition" in weather_get
    assert "pblHeight: boundary_layer_height" in weather_get
    assert "latitude: reader.modelLat" in weather_get
    assert "convectiveInhibition: Variable?" in derived_mapping
    assert "boundaryLayerHeight: Variable?" in derived_mapping
    assert "latitude: reader.modelLat" in derived_mapping

    assert "pblHeight: try await get(variable: boundaryLayerHeight" in derived_mapping


def test_all_weather_code_call_sites_use_current_api_signature():
    def read_balanced_call(source: str, index: int) -> str:
        depth = 0
        for pos in range(index, len(source)):
            char = source[pos]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return source[index : pos + 1]
        raise AssertionError("Unbalanced WeatherCode.calculate call")

    for path in (ROOT / "vendor" / "open-meteo" / "Sources" / "App").rglob("*.swift"):
        if path.name == "WeatherCode.swift":
            continue
        source = path.read_text(encoding="utf-8")
        if "WeatherCode.calculate(" not in source:
            continue

        start = 0
        while True:
            index = source.find("WeatherCode.calculate(", start)
            if index < 0:
                break
            call = read_balanced_call(source, index)
            if call == "WeatherCode.calculate()":
                start = index + len("WeatherCode.calculate(")
                continue
            assert "convectiveInhibition:" in call, f"{path} has an old WeatherCode.calculate call"
            assert "pblHeight:" in call, f"{path} has an old WeatherCode.calculate call"
            assert "latitude:" in call, f"{path} has an old WeatherCode.calculate call"
            start = index + len("WeatherCode.calculate(")


def test_layer_builders_are_split_by_source_product():
    gfs = (ROOT / "scripts" / "build_openmeteo_gfs_layers.sh").read_text(encoding="utf-8")
    cams = (ROOT / "scripts" / "build_openmeteo_cams_layers.sh").read_text(encoding="utf-8")

    assert "data/public" in gfs
    assert "data/public" in cams
    for script in (gfs, cams):
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


def test_combined_production_cycle_is_removed_in_favor_of_split_source_cycles():
    assert not (ROOT / "scripts" / "run_openmeteo_production_cycle.sh").exists()
    assert not (ROOT / "scripts" / "download_openmeteo_runtime_data.sh").exists()
    assert not (ROOT / "scripts" / "build_server_openmeteo_layers.sh").exists()
    assert not (ROOT / "scripts" / "run_cams_production_cycle.sh").exists()
    assert not (ROOT / "scripts" / "run_cams_scheduled_cycle.sh").exists()

    split_scripts = (
        ROOT / "scripts" / "run_gfs_production_cycle.sh",
        ROOT / "scripts" / "run_cams_ftp_production_cycle.sh",
        ROOT / "scripts" / "run_cams_ads_production_cycle.sh",
    )
    for path in split_scripts:
        script = path.read_text(encoding="utf-8")
        assert "scripts/download_openmeteo_runtime_data.sh" not in script
        assert "scripts/build_server_openmeteo_layers.sh" not in script
        assert "scripts/deploy_singapore_candidate.sh" not in script
        assert "restart local Open-Meteo API" not in script
        assert "download runtime data run=$RUN start=" in script
        assert "download runtime data run=$RUN end=" in script
        assert "flock -n" in script


def test_gfs_probe_cycle_uses_official_indices_before_gfs_only_production():
    probe = (ROOT / "scripts" / "probe_gfs_official_run.py").read_text(encoding="utf-8")
    cycle = (ROOT / "scripts" / "run_gfs_probe_and_cycle.sh").read_text(encoding="utf-8")
    production = (ROOT / "scripts" / "run_gfs_production_cycle.sh").read_text(encoding="utf-8")
    download = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(encoding="utf-8")

    assert "noaa-gfs-bdp-pds.s3.amazonaws.com" in probe
    assert "nomads.ncep.noaa.gov" not in probe
    assert "sfluxgrbf{fff}.grib2.idx" in probe
    assert "pgrb2.0p25.f{fff}.idx" in probe
    assert "pgrb2b.0p25.f{fff}.idx" in probe
    assert "--download-from-aws" in download
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


def test_gfs_production_publishes_only_after_bound_domains_and_layers_succeed():
    production = (ROOT / "scripts" / "run_gfs_production_cycle.sh").read_text(encoding="utf-8")

    assert "prepare_gfs_staging_data_dir()" in production
    assert "restore_gfs_publish_backup()" in production
    assert "publish_gfs_products()" in production
    assert "gfs_publish_ok=false" in production
    assert "gfs_publish_ok=true" in production
    assert 'for domain in ncep_gfs013 ncep_gfs025; do' in production
    assert "cams_global" not in production
    assert "scripts/download_openmeteo_gfs_data.sh" in production
    assert "scripts/build_openmeteo_gfs_layers.sh" in production
    assert "--domains ncep_gfs013,ncep_gfs025" in production
    assert production.index("bash scripts/download_openmeteo_gfs_data.sh") < production.index(
        "--domains ncep_gfs013,ncep_gfs025"
    )
    assert production.index("--domains ncep_gfs013,ncep_gfs025") < production.index(
        "bash scripts/build_openmeteo_gfs_layers.sh"
    )
    publish_call = production.index("\n  publish_gfs_products\n")
    assert production.index("bash scripts/build_openmeteo_gfs_layers.sh") < publish_call
    assert publish_call < production.index("gfs_publish_ok=true")


def test_cams_ftp_scheduled_cycle_probes_remote_batches_like_gfs():
    scheduled = (ROOT / "scripts" / "run_cams_ftp_scheduled_cycle.sh").read_text(encoding="utf-8")
    probe = (ROOT / "scripts" / "probe_cams_ftp_run.py").read_text(encoding="utf-8")
    production = (ROOT / "scripts" / "run_cams_ftp_production_cycle.sh").read_text(encoding="utf-8")
    download = (ROOT / "scripts" / "download_openmeteo_cams_data.sh").read_text(encoding="utf-8")

    assert "scripts/probe_cams_ftp_run.py" in scheduled
    assert "WEATHER_CAMS_SOURCE" not in scheduled
    assert "ftp|ecpds|ftp_ecpds)" not in scheduled
    assert "ads|cds|ads_cds)" not in scheduled
    assert "CAMS FTP/ECPDS production cycle already running, skip probe." in scheduled
    assert "datetime.now(timezone.utc)" not in scheduled
    assert "now.hour >= 22" not in scheduled
    assert "now.hour >= 10" not in scheduled
    assert "scripts/run_cams_ftp_production_cycle.sh" in scheduled
    assert "scripts/probe_cams_ftp_run.py --data-dir" in scheduled
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
    assert "run_cams_production_cycle.sh" not in scheduled
    assert "run_cams_scheduled_cycle.sh" not in scheduled
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


def test_cams_ads_scheduled_cycle_keeps_fixed_utc_target_logic():
    scheduled = (ROOT / "scripts" / "run_cams_ads_scheduled_cycle.sh").read_text(encoding="utf-8")
    production = (ROOT / "scripts" / "run_cams_ads_production_cycle.sh").read_text(encoding="utf-8")
    download = (ROOT / "scripts" / "download_openmeteo_cams_ads_data.sh").read_text(encoding="utf-8")

    assert "datetime.now(timezone.utc)" in scheduled
    assert "now.hour >= 22" in scheduled
    assert "now.hour >= 10" in scheduled
    assert "scripts/probe_cams_ftp_run.py" not in scheduled
    assert "scripts/run_cams_ads_production_cycle.sh" in scheduled
    assert "scripts/run_cams_ftp_production_cycle.sh" not in scheduled
    assert "CAMS ADS/CDS production cycle already running, skip." in scheduled
    assert "greenhouse_run=" in scheduled
    assert "cams_global_greenhouse_gases --min-frames 41" in scheduled
    assert "scripts/download_openmeteo_cams_ads_data.sh" in production
    assert "scripts/download_openmeteo_cams_data.sh" not in production
    assert "CAMS_GREENHOUSE_RUN=" in production
    assert "cams_global_greenhouse_gases" in production
    assert "run_to_greenhouse_run" in production
    assert "cams_global_greenhouse_gases \\\n    --min-frames 41" in production
    assert "download-cams-ads cams_global" in download
    assert "download-cams cams_global" not in download


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
