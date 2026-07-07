import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_openmeteo_official_50point_batches.py"
LOCAL_API_VALIDATE_ARGS = {
    "local_openmeteo_mode": "api",
    "data_dir": ROOT,
    "output_dir": ROOT,
    "openmeteo_image": "image",
    "openmeteo_tag": "tag",
    "direct_ssh_host": None,
    "direct_remote_root": None,
}


def load_module():
    spec = importlib.util.spec_from_file_location("validate_openmeteo_official_50point_batches", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_gfs_official_window_starts_at_run_and_covers_requested_end():
    validator = load_module()

    window = validator.gfs_official_window(
        gfs_run="2026-06-29T06:00",
        requested_start_hour="2026-06-29T14:00",
        requested_frames=50,
    )

    assert window == {
        "start_hour": "2026-06-29T06:00",
        "end_hour": "2026-07-01T15:00",
        "frames": 58,
    }


def test_gfs_validation_uses_pinned_run_locally_and_for_reference(monkeypatch):
    validator = load_module()

    seen_params = []

    def fake_fetch_hourlies(**kwargs):
        assert kwargs["params"]["latitude"] == "10.0"
        seen_params.append(kwargs["params"])
        return [
            {
                "time": ["2026-06-29T06:00", "2026-06-29T07:00", "2026-06-29T08:00"],
                "temperature_2m": [27.0, 28.0, 29.0],
            }
        ]

    assert not hasattr(validator, "trim_hourly_window")
    monkeypatch.setattr(validator, "fetch_hourlies", fake_fetch_hourlies)

    report = validator.validate_scope_batch(
        scope="gfs",
        batch_index=1,
        points=[{"latitude": 10.0, "longitude": 100.0}],
        variables=["temperature_2m"],
        api_base_url="local",
        **LOCAL_API_VALIDATE_ARGS,
        reference_base_url="official",
        reference_ssh_host=None,
        gfs_run="2026-06-29T06:00",
        start_hour="2026-06-29T06:00",
        end_hour="2026-06-29T08:00",
        frames=3,
        chunk_size=10,
        tolerance=0.001,
        timeout=1,
        retries=0,
        retry_delay=0,
        request_pause=0,
        gfs_model="gfs013",
    )

    assert report["passed"] is True
    assert report["checked_values"] == 3
    assert seen_params[0]["models"] == "gfs013"
    assert seen_params[0]["run"] == "2026-06-29T06:00"
    assert seen_params[0]["forecast_hours"] == 3
    assert "start_hour" not in seen_params[0]
    assert "end_hour" not in seen_params[0]
    assert seen_params[1]["models"] == "gfs013"
    assert seen_params[1]["run"] == "2026-06-29T06:00"
    assert seen_params[1]["forecast_hours"] == 3
    assert "start_hour" not in seen_params[1]


def test_gfs_validation_can_use_latest_reference_window(monkeypatch):
    validator = load_module()

    seen_params = []

    def fake_fetch_hourlies(**kwargs):
        seen_params.append(kwargs["params"])
        return [
            {
                "time": ["2026-06-29T06:00", "2026-06-29T07:00"],
                "temperature_2m": [27.0, 28.0],
            }
        ]

    monkeypatch.setattr(validator, "fetch_hourlies", fake_fetch_hourlies)

    report = validator.validate_scope_batch(
        scope="gfs",
        batch_index=1,
        points=[{"latitude": 10.0, "longitude": 100.0}],
        variables=["temperature_2m"],
        api_base_url="local",
        **LOCAL_API_VALIDATE_ARGS,
        reference_base_url="official",
        reference_ssh_host=None,
        gfs_run="2026-06-29T06:00",
        gfs_reference_mode="latest",
        start_hour="2026-06-29T06:00",
        end_hour="2026-06-29T07:00",
        frames=2,
        chunk_size=10,
        tolerance=0.001,
        timeout=1,
        retries=0,
        retry_delay=0,
        request_pause=0,
        gfs_model="gfs013",
    )

    assert report["passed"] is True
    assert seen_params[0]["start_hour"] == "2026-06-29T06:00"
    assert seen_params[0]["end_hour"] == "2026-06-29T07:00"
    assert "run" not in seen_params[0]
    assert seen_params[1]["start_hour"] == "2026-06-29T06:00"
    assert seen_params[1]["end_hour"] == "2026-06-29T07:00"
    assert "run" not in seen_params[1]
    assert "forecast_hours" not in seen_params[1]


def test_cams_validation_uses_date_window_for_air_quality_api(monkeypatch):
    validator = load_module()

    seen_params = []

    def fake_fetch_hourlies(**kwargs):
        seen_params.append(kwargs["params"])
        first = 99.0 if kwargs["base_url"] == "local" else 12.0
        return [
            {
                "time": ["2026-07-04T00:00", "2026-07-04T01:00", "2026-07-04T02:00"],
                "pm10": [first, 13.0, 14.0],
            }
        ]

    monkeypatch.setattr(validator, "fetch_hourlies", fake_fetch_hourlies)

    report = validator.validate_scope_batch(
        scope="cams",
        batch_index=1,
        points=[{"latitude": 10.0, "longitude": 100.0}],
        variables=["pm10"],
        api_base_url="local",
        **LOCAL_API_VALIDATE_ARGS,
        reference_base_url="official",
        reference_ssh_host=None,
        gfs_run="2026-06-29T06:00",
        start_hour="2026-07-04T01:00",
        end_hour="2026-07-04T02:00",
        frames=2,
        chunk_size=10,
        tolerance=0.001,
        timeout=1,
        retries=0,
        retry_delay=0,
        request_pause=0,
        gfs_model="gfs013",
    )

    assert report["passed"] is True
    assert len(seen_params) == 2
    for params in seen_params:
        assert params["domains"] == "cams_global"
        assert params["start_date"] == "2026-07-04"
        assert params["end_date"] == "2026-07-04"
        assert "start_hour" not in params
        assert "end_hour" not in params


def test_cams_multilevel_intermediate_hours_are_diagnostic_not_strict_failures(monkeypatch):
    validator = load_module()

    def fake_fetch_hourlies(**kwargs):
        values = [10.0, 20.0, 30.0] if kwargs["base_url"] == "local" else [10.0, 99.0, 88.0]
        return [
            {
                "time": ["2026-07-04T00:00", "2026-07-04T01:00", "2026-07-04T02:00"],
                "nitrogen_dioxide": values,
            }
        ]

    monkeypatch.setattr(validator, "fetch_hourlies", fake_fetch_hourlies)

    report = validator.validate_scope_batch(
        scope="cams",
        batch_index=1,
        points=[{"latitude": 10.0, "longitude": 100.0}],
        variables=["nitrogen_dioxide"],
        api_base_url="local",
        **LOCAL_API_VALIDATE_ARGS,
        reference_base_url="official",
        reference_ssh_host=None,
        gfs_run="2026-06-29T06:00",
        start_hour="2026-07-04T00:00",
        end_hour="2026-07-04T02:00",
        frames=3,
        chunk_size=10,
        tolerance=0.001,
        timeout=1,
        retries=0,
        retry_delay=0,
        request_pause=0,
        gfs_model="gfs013",
    )

    assert report["passed"] is True
    assert report["failures"] == []
    assert report["checked_values"] == 1
    assert report["diagnostic_values"] == 2
    assert report["diagnostic_differences"] == [
        {
            "reason": "cams_interpolated_frame_difference",
            "batch": 1,
            "scope": "cams",
            "point_index": 0,
            "point": {"latitude": 10.0, "longitude": 100.0},
            "variable": "nitrogen_dioxide",
            "mismatch_count": 2,
            "first_mismatches": [
                {"frame": 1, "local": 20.0, "reference": 99.0, "reason": "value_mismatch"},
                {"frame": 2, "local": 30.0, "reference": 88.0, "reason": "value_mismatch"},
            ],
        }
    ]


def test_failed_point_count_deduplicates_multiple_variable_failures():
    validator = load_module()

    failures = [
        {"reason": "reference_mismatch", "point_index": 0, "variable": "temperature_2m"},
        {"reason": "reference_mismatch", "point_index": 0, "variable": "wind_speed_10m"},
        {"reason": "reference_mismatch", "point_index": 3, "variable": "temperature_2m"},
        {"reason": "point_count_mismatch"},
    ]

    assert validator.failed_point_count(failures) == 2


def test_build_validation_points_covers_unique_random_grid_and_off_grid_samples():
    validator = load_module()

    points = validator.build_validation_points(
        total_points=1000,
        left_lon=70.0,
        right_lon=140.0,
        bottom_lat=0.0,
        top_lat=58.0,
        seed=20260703,
        grid_point_ratio=0.25,
    )

    assert len(points) == 1000
    assert len({(point["latitude"], point["longitude"]) for point in points}) == 1000

    def is_quarter_degree(value):
        return abs(value * 4 - round(value * 4)) < 1e-6

    grid_points = [
        point
        for point in points
        if is_quarter_degree(point["latitude"]) and is_quarter_degree(point["longitude"])
    ]
    off_grid_points = [
        point
        for point in points
        if not (is_quarter_degree(point["latitude"]) and is_quarter_degree(point["longitude"]))
    ]

    assert len(grid_points) >= 200
    assert len(off_grid_points) >= 700


def test_failure_gate_continues_until_failed_point_threshold_is_exceeded():
    validator = load_module()

    assert validator.should_stop_for_batch_failures(
        {"failures": [{"point_index": 1}, {"point_index": 1}, {"point_index": 2}], "failed_points": 2},
        max_failed_points_per_batch=2,
    ) is False
    assert validator.should_stop_for_batch_failures(
        {"failures": [{"point_index": 1}, {"point_index": 2}, {"point_index": 3}], "failed_points": 3},
        max_failed_points_per_batch=2,
    ) is True


def test_failure_gate_stops_immediately_on_non_point_failures():
    validator = load_module()

    assert validator.should_stop_for_batch_failures(
        {"failures": [{"reason": "point_count_mismatch"}], "failed_points": 0},
        max_failed_points_per_batch=2,
    ) is True


def test_parse_scopes_requires_known_scope_names():
    validator = load_module()

    assert validator.parse_scopes("gfs,cams") == {"gfs", "cams"}
    assert validator.parse_scopes(" gfs ") == {"gfs"}

    try:
        validator.parse_scopes("gfs,satellite")
    except ValueError as exc:
        assert "satellite" in str(exc)
    else:
        raise AssertionError("invalid scope should fail")


def test_default_gfs_pressure_compare_levels_cover_full_product_contract():
    validator = load_module()

    assert validator.DEFAULT_OFFICIAL_GFS_PRESSURE_COMPARE_LEVELS_HPA == (
        1000,
        975,
        950,
        925,
        900,
        850,
        800,
        750,
        700,
        650,
        600,
        550,
        500,
        400,
        300,
        200,
    )


def test_missing_runtime_dirs_do_not_claim_variables(tmp_path):
    validator = load_module()

    assert validator.actual_pressure_candidates(["temperature_850hPa"], tmp_path) == []
    assert validator.actual_cams_candidates(["pm2_5"], tmp_path) == []


def test_actual_pressure_candidates_follow_existing_om_level_dirs(tmp_path):
    validator = load_module()
    gfs025 = tmp_path / "ncep_gfs025"
    for name in (
        "temperature_1000hPa",
        "relative_humidity_850hPa",
        "wind_u_component_850hPa",
        "wind_v_component_850hPa",
    ):
        (gfs025 / name).mkdir(parents=True)

    candidates = validator.actual_pressure_candidates(
        [
            "temperature_10hPa",
            "temperature_1000hPa",
            "relativehumidity_850hPa",
            "wind_speed_850hPa",
            "wind_direction_850hPa",
            "wind_speed_700hPa",
        ],
        tmp_path,
    )

    assert candidates == [
        "temperature_1000hPa",
        "relativehumidity_850hPa",
        "wind_speed_850hPa",
        "wind_direction_850hPa",
    ]


def test_official_pressure_candidates_filter_product_only_levels(tmp_path):
    validator = load_module()
    gfs025 = tmp_path / "ncep_gfs025"
    for name in (
        "temperature_1000hPa",
        "temperature_975hPa",
        "temperature_925hPa",
        "wind_u_component_900hPa",
        "wind_v_component_900hPa",
        "wind_u_component_850hPa",
        "wind_v_component_850hPa",
    ):
        (gfs025 / name).mkdir(parents=True)

    candidates = validator.actual_pressure_candidates(
        [
            "temperature_1000hPa",
            "temperature_975hPa",
            "temperature_925hPa",
            "wind_speed_900hPa",
            "wind_speed_850hPa",
        ],
        tmp_path,
        compare_levels_hpa=validator.parse_level_csv("1000,925,850"),
    )

    assert candidates == [
        "temperature_1000hPa",
        "temperature_925hPa",
        "wind_speed_850hPa",
    ]


def test_gfs_candidates_include_actual_raw_surface_variables(monkeypatch, tmp_path):
    validator = load_module()
    gfs013 = tmp_path / "ncep_gfs013"
    for name in (
        "wind_u_component_10m",
        "wind_v_component_10m",
        "temperature_2m",
        "relative_humidity_2m",
    ):
        (gfs013 / name).mkdir(parents=True)

    inventory = {
        "gfs_runtime_data": {
            "surface_variables": ["wind_u_component_10m", "wind_v_component_10m", "temperature_2m"],
            "pressure_variables": [],
        },
        "gfs_point_api": {
            "surface_variables": ["wind_speed_10m", "dew_point_2m"],
            "pressure_variables": [],
        },
        "air_quality": {"raw_variables": [], "derived_variables": []},
    }
    monkeypatch.setattr(validator, "build_inventory", lambda repo_root: inventory)

    candidates = validator.candidate_variables(ROOT, "gfs", tmp_path)

    assert candidates == [
        "wind_u_component_10m",
        "wind_v_component_10m",
        "temperature_2m",
        "wind_speed_10m",
        "dew_point_2m",
    ]


def test_gfs_candidates_exclude_internal_raw_variables_not_exposed_by_forecast_api(monkeypatch, tmp_path):
    validator = load_module()
    gfs025 = tmp_path / "ncep_gfs025"
    for name in ("categorical_freezing_rain", "freezing_level_height"):
        (gfs025 / name).mkdir(parents=True)

    inventory = {
        "gfs_runtime_data": {
            "surface_variables": ["categorical_freezing_rain", "freezing_level_height"],
            "pressure_variables": [],
        },
        "gfs_point_api": {"surface_variables": [], "pressure_variables": []},
        "air_quality": {"raw_variables": [], "derived_variables": []},
    }
    monkeypatch.setattr(validator, "build_inventory", lambda repo_root: inventory)

    assert validator.candidate_variables(ROOT, "gfs", tmp_path) == ["freezing_level_height"]
