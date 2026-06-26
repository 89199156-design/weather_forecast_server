import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "validate_openmeteo_point_api.py"
    spec = importlib.util.spec_from_file_location("validate_openmeteo_point_api", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_generate_points_is_deterministic_inside_configured_region():
    validator = load_module()

    points = validator.generate_points(3, left_lon=70, right_lon=140, bottom_lat=0, top_lat=58)

    assert points == [
        {"latitude": 9.666667, "longitude": 81.666667},
        {"latitude": 29.0, "longitude": 105.0},
        {"latitude": 48.333333, "longitude": 128.333333},
    ]


def test_generate_points_can_offset_gate_samples_to_avoid_overlap():
    validator = load_module()

    baseline = validator.generate_points(50, left_lon=70, right_lon=140, bottom_lat=0, top_lat=58)
    shifted = validator.generate_points(100, left_lon=70, right_lon=140, bottom_lat=0, top_lat=58, point_offset=0.25)

    assert set(tuple(point.items()) for point in baseline).isdisjoint(
        set(tuple(point.items()) for point in shifted)
    )


def test_chunked_inventory_keeps_gfs_and_cams_variables_separate():
    validator = load_module()
    inventory = {
        "forecast": {
            "surface_api_variables": ["temperature_2m", "uv_index", "pm10", "wave_height"],
            "pressure_api_variables": ["temperature_850hPa", "wave_height_850hPa"],
        },
        "gfs_point_api": {
            "surface_variables": ["temperature_2m", "uv_index"],
            "pressure_variables": ["temperature_850hPa"],
        },
        "air_quality": {
            "raw_variables": ["pm2_5"],
            "derived_variables": ["us_aqi"],
        },
    }

    assert validator.variables_for_scope(inventory, "gfs") == ["temperature_2m", "uv_index", "temperature_850hPa"]
    assert validator.variables_for_scope(inventory, "cams") == ["pm2_5", "us_aqi"]
    assert validator.chunked(["a", "b", "c"], 2) == [["a", "b"], ["c"]]


def test_compare_series_reports_value_null_and_length_mismatches():
    validator = load_module()

    assert validator.compare_series([1.0, None, 3.0], [1.00001, None, 3.0], frames=3, tolerance=0.001) == []
    mismatches = validator.compare_series([1.0, None, 3.0], [2.0, 4.0], frames=3, tolerance=0.001)

    assert mismatches == [
        {"frame": 0, "local": 1.0, "reference": 2.0, "reason": "value_mismatch"},
        {"frame": 1, "local": None, "reference": 4.0, "reason": "null_mismatch"},
        {"frame": 2, "local": 3.0, "reference": None, "reason": "length_mismatch"},
    ]


def test_summarize_variable_detects_missing_and_all_null_local_output():
    validator = load_module()

    assert validator.summarize_variable(None, frames=2) == {"status": "missing", "frames": 0, "nulls": 2}
    assert validator.summarize_variable([None, None, 3], frames=2) == {"status": "all_null", "frames": 2, "nulls": 2}
    assert validator.summarize_variable([None, 1, 2], frames=2) == {"status": "ok", "frames": 2, "nulls": 1}


def test_request_params_batches_multiple_points():
    validator = load_module()
    endpoint, params = validator.request_params(
        "gfs",
        [
            {"latitude": 10.0, "longitude": 100.0},
            {"latitude": 20.5, "longitude": 110.25},
        ],
        ["temperature_2m"],
        2,
    )

    assert endpoint == "/v1/forecast"
    assert params["latitude"] == "10.0,20.5"
    assert params["longitude"] == "100.0,110.25"
    assert params["models"] == "gfs_global"


def test_validate_scope_batches_points_and_compares_each_location(monkeypatch):
    validator = load_module()
    calls = []

    def fake_fetch_json(base_url, endpoint, params, **_kwargs):
        calls.append((base_url, endpoint, params["latitude"], params["longitude"]))
        point_count = len(params["latitude"].split(","))
        return [{"hourly": {"temperature_2m": [1.0, 2.0]}} for _ in range(point_count)]

    monkeypatch.setattr(validator, "fetch_json", fake_fetch_json)
    points = [
        {"latitude": 10.0, "longitude": 100.0},
        {"latitude": 20.0, "longitude": 110.0},
        {"latitude": 30.0, "longitude": 120.0},
    ]

    report = validator.validate_scope(
        api_base_url="local",
        reference_base_url="reference",
        scope="gfs",
        variables=["temperature_2m"],
        points=points,
        frames=2,
        chunk_size=10,
        point_chunk_size=2,
        sample_offset=0.0,
        tolerance=0.001,
        timeout=1,
        allow_all_null=False,
        request_retries=0,
        request_retry_delay=0,
        request_pause=0,
    )

    assert report["passed"] is True
    assert report["checked_values"] == 6
    assert calls == [
        ("local", "/v1/forecast", "10.0,20.0", "100.0,110.0"),
        ("reference", "/v1/forecast", "10.0,20.0", "100.0,110.0"),
        ("local", "/v1/forecast", "30.0", "120.0"),
        ("reference", "/v1/forecast", "30.0", "120.0"),
    ]


def test_validate_scope_allows_all_null_when_reference_matches(monkeypatch):
    validator = load_module()

    def fake_fetch_json(base_url, endpoint, params, **_kwargs):
        return {"hourly": {"mass_density_8m": [None, None]}}

    monkeypatch.setattr(validator, "fetch_json", fake_fetch_json)

    report = validator.validate_scope(
        api_base_url="local",
        reference_base_url="reference",
        scope="gfs",
        variables=["mass_density_8m"],
        points=[{"latitude": 10.0, "longitude": 100.0}],
        frames=2,
        chunk_size=10,
        point_chunk_size=1,
        sample_offset=0.0,
        tolerance=0.001,
        timeout=1,
        allow_all_null=False,
        request_retries=0,
        request_retry_delay=0,
        request_pause=0,
    )

    assert report["passed"] is True
    assert report["failures"] == []


def test_validate_scope_rejects_all_null_when_reference_has_values(monkeypatch):
    validator = load_module()

    def fake_fetch_json(base_url, endpoint, params, **_kwargs):
        values = [None, None] if base_url == "local" else [1.0, 2.0]
        return {"hourly": {"mass_density_8m": values}}

    monkeypatch.setattr(validator, "fetch_json", fake_fetch_json)

    report = validator.validate_scope(
        api_base_url="local",
        reference_base_url="reference",
        scope="gfs",
        variables=["mass_density_8m"],
        points=[{"latitude": 10.0, "longitude": 100.0}],
        frames=2,
        chunk_size=10,
        point_chunk_size=1,
        sample_offset=0.0,
        tolerance=0.001,
        timeout=1,
        allow_all_null=False,
        request_retries=0,
        request_retry_delay=0,
        request_pause=0,
    )

    assert report["passed"] is False
    assert report["failures"][0]["reason"] == "reference_mismatch"
