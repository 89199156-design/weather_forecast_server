from pathlib import Path
import json
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from publish_ecmwf_native_coverage import (
    ensemble_source_run_plan,
    expected_forecast_hours,
    grid_contract,
)
import publish_ecmwf_native_coverage as publisher


def test_ecmwf_ensemble_plan_keeps_five_consecutive_cycles() -> None:
    plan = ensemble_source_run_plan("2026073000")

    assert plan == (
        ("2026072900", 360),
        ("2026072906", 144),
        ("2026072912", 360),
        ("2026072918", 144),
        ("2026073000", 360),
    )


def test_ecmwf_probability_schedule_skips_undefined_hour_zero() -> None:
    deterministic = expected_forecast_hours("2026073000", 360)
    probability = expected_forecast_hours(
        "2026073000",
        360,
        include_hour_zero=False,
    )

    assert deterministic[0] == 0
    assert probability[0] == 3
    assert deterministic[-1] == probability[-1] == 360
    assert len(deterministic) == len(probability) + 1


def test_ecmwf_gust_support_schedule_matches_available_original_frames() -> None:
    hours = expected_forecast_hours("2026072712", 186)

    assert hours == [*range(3, 91, 3), *range(150, 187, 6)]
    assert len(hours) == 37
    assert 93 not in hours
    assert 144 not in hours


def test_ecmwf_short_ensemble_cycle_uses_three_hour_frames() -> None:
    hours = expected_forecast_hours(
        "2026072918",
        144,
        include_hour_zero=False,
    )

    assert hours == list(range(3, 145, 3))


def test_ecmwf_grid_matches_upstream_native_storage_contract() -> None:
    grid = grid_contract()

    assert (grid["nx"], grid["ny"]) == (297, 249)
    assert grid["dt_seconds"] == 3 * 3600
    assert grid["om_file_length"] == 104


def test_ecmwf_validator_accepts_real_tuple_dimensions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_root = (
        tmp_path
        / "data_run"
        / "ecmwf_ifs025_ensemble"
        / "2026"
        / "07"
        / "30"
        / "0000Z"
    )
    run_root.mkdir(parents=True)
    (run_root / "precipitation_probability.om").write_bytes(b"om")
    (run_root / "meta.json").write_text(
        json.dumps(
            {
                "reference_time": "2026-07-30T00:00:00Z",
                "valid_times": ["2026-07-30T03:00:00Z"],
                "variables": ["precipitation_probability"],
            }
        ),
        encoding="utf-8",
    )
    static = tmp_path / "ecmwf_ifs025" / "static" / "HSURF.om"
    static.parent.mkdir(parents=True)
    static.write_bytes(b"om")
    monkeypatch.setattr(
        publisher,
        "read_array_dimensions",
        lambda path: (
            (249, 297)
            if Path(path).name == "HSURF.om"
            else (249, 297, 1)
        ),
    )

    publisher.validate_run(
        tmp_path,
        "ecmwf_ifs025_ensemble",
        "2026073000",
        3,
        {"precipitation_probability"},
    )
    publisher.validate_static(tmp_path)


def test_ecmwf_validator_accounts_for_undefined_hour_zero_frames(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_root = (
        tmp_path
        / "data_run"
        / "ecmwf_ifs025"
        / "2026"
        / "07"
        / "29"
        / "0600Z"
    )
    run_root.mkdir(parents=True)
    variables = {"temperature_2m", "precipitation", "wind_gusts_10m"}
    for variable in variables:
        (run_root / f"{variable}.om").write_bytes(b"om")
    (run_root / "meta.json").write_text(
        json.dumps(
            {
                "reference_time": "2026-07-29T06:00:00Z",
                "valid_times": [
                    "2026-07-29T06:00:00Z",
                    "2026-07-29T09:00:00Z",
                    "2026-07-29T12:00:00Z",
                ],
                "variables": sorted(variables),
            }
        ),
        encoding="utf-8",
    )

    def dimensions(path: Path) -> tuple[int, ...]:
        return (
            (249, 297, 2)
            if Path(path).stem in {"precipitation", "wind_gusts_10m"}
            else (249, 297, 3)
        )

    monkeypatch.setattr(publisher, "read_array_dimensions", dimensions)
    publisher.validate_run(
        tmp_path,
        "ecmwf_ifs025",
        "2026072906",
        6,
        variables,
    )


def test_ecmwf_validator_does_not_relax_hour_zero_for_instant_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_root = (
        tmp_path
        / "data_run"
        / "ecmwf_ifs025"
        / "2026"
        / "07"
        / "29"
        / "0600Z"
    )
    run_root.mkdir(parents=True)
    (run_root / "temperature_2m.om").write_bytes(b"om")
    (run_root / "meta.json").write_text(
        json.dumps(
            {
                "reference_time": "2026-07-29T06:00:00Z",
                "valid_times": [
                    "2026-07-29T06:00:00Z",
                    "2026-07-29T09:00:00Z",
                    "2026-07-29T12:00:00Z",
                ],
                "variables": ["temperature_2m"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        publisher,
        "read_array_dimensions",
        lambda _path: (249, 297, 2),
    )

    try:
        publisher.validate_run(
            tmp_path,
            "ecmwf_ifs025",
            "2026072906",
            6,
            {"temperature_2m"},
        )
    except ValueError as exc:
        assert "expected=(249, 297, 3)" in str(exc)
    else:
        raise AssertionError("instantaneous field dimension mismatch was accepted")


def test_ecmwf_cycle_recovers_webp_without_republishing_immutable_om() -> None:
    source = (SCRIPTS / "run_ecmwf_native_production_cycle.sh").read_text(
        encoding="utf-8"
    )

    assert 'REUSE_PUBLISHED=false' in source
    assert 'reuse published immutable OM' in source
    assert 'if [[ "$REUSE_PUBLISHED" != "true" ]]; then' in source
    assert 'WEATHER_ECMWF_DEFER_CONSUMERS:-false' in source
    assert 'Rust consumers intentionally deferred for first migration run' in source
    assert '"$CURRENT_MARKER" -ef "$API_MARKER"' in source
    assert 'bash "$WEBP_RUNNER" ecmwf' in source
    assert 'reload_native_api_snapshot.sh" \\\n    ecmwf "$EXPECTED_COVERAGE_ID"' in source
    assert '"io.weather-forecast.ecmwf-patch-sha256": sys.argv[2]' in source
    assert '"io.weather-forecast.ecmwf-source-id": sys.argv[3]' in source
    assert "ECMWF native image provenance mismatch" in source
    assert 'raw_variables_for_horizon(int(sys.argv[1]))' in source
    assert '--only-variables "$RUN_VARIABLES"' in source


def test_ecmwf_probe_uses_rust_webp_marker_contract() -> None:
    source = (SCRIPTS / "run_ecmwf_probe_and_cycle.sh").read_text(
        encoding="utf-8"
    )

    assert 'current/ecmwf_ifs025.json' in source
    assert 'webp.get("scope") == "ecmwf_ifs025"' in source
    assert 'len(webp.get("layers") or []) == 18' in source
