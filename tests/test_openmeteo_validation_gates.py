import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "run_openmeteo_validation_gates.py"
    spec = importlib.util.spec_from_file_location("run_openmeteo_validation_gates", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_validation_gate_commands_stop_after_first_failed_gate():
    runner = load_module()
    commands = runner.build_gate_commands(
        api_base_url="http://127.0.0.1:18080",
        reference_base_url="https://api.open-meteo.com",
        output_dir=Path("reports"),
        scopes=["gfs", "cams"],
        point_gates=[50, 100, 500],
        frames=50,
        start_hour="2026-06-26T10:00",
        end_hour="2026-06-28T11:00",
        point_chunk_size=25,
        request_retries=5,
        request_retry_delay=3.0,
        request_pause=0.25,
    )

    assert commands[0]["points"] == 50
    assert commands[0]["scope"] == "gfs"
    assert commands[1]["points"] == 50
    assert commands[1]["scope"] == "cams"
    assert commands[2]["points"] == 100
    assert commands[-1]["points"] == 500
    assert all("--reference-base-url" in " ".join(command["argv"]) for command in commands)
    assert all("--frames" in " ".join(command["argv"]) for command in commands)
    assert all("--start-hour 2026-06-26T10:00" in " ".join(command["argv"]) for command in commands)
    assert all("--end-hour 2026-06-28T11:00" in " ".join(command["argv"]) for command in commands)
    assert all("--point-chunk-size 25" in " ".join(command["argv"]) for command in commands)
    assert all("--request-retries 5" in " ".join(command["argv"]) for command in commands)
    assert all("--request-pause 0.25" in " ".join(command["argv"]) for command in commands)
    assert "--point-offset 0.0" in " ".join(commands[0]["argv"])
    assert "--point-offset 0.25" in " ".join(commands[2]["argv"])
    assert "--point-offset 0.5" in " ".join(commands[-1]["argv"])


def test_default_gate_offsets_keep_point_sets_disjoint():
    runner = load_module()

    offsets = runner.default_gate_offsets([50, 100, 500])

    assert offsets == {50: 0.0, 100: 0.25, 500: 0.5}


def test_validation_gate_commands_can_pin_single_run_reference():
    runner = load_module()

    commands = runner.build_gate_commands(
        api_base_url="http://127.0.0.1:18080",
        reference_base_url="https://single-runs-api.open-meteo.com",
        output_dir=Path("reports"),
        scopes=["gfs"],
        point_gates=[100],
        frames=50,
        start_hour="2026-06-26T10:00",
        end_hour="2026-06-28T11:00",
        point_chunk_size=25,
        request_retries=5,
        request_retry_delay=3.0,
        request_pause=0.25,
        api_host_header="single-runs-api.open-meteo.com",
        reference_host_header="single-runs-api.open-meteo.com",
        run="2026-06-26T00:00",
    )

    argv = " ".join(commands[0]["argv"])
    assert "--api-host-header single-runs-api.open-meteo.com" in argv
    assert "--reference-host-header single-runs-api.open-meteo.com" in argv
    assert "--run 2026-06-26T00:00" in argv


def test_validation_summary_marks_first_failure_and_skipped_gates():
    runner = load_module()
    summary = runner.summarize_results(
        [
            {"points": 50, "scope": "gfs", "exit_code": 0, "report": "50-gfs.json"},
            {"points": 50, "scope": "cams", "exit_code": 1, "report": "50-cams.json"},
        ],
        planned=[
            {"points": 50, "scope": "gfs", "report": "50-gfs.json"},
            {"points": 50, "scope": "cams", "report": "50-cams.json"},
            {"points": 100, "scope": "gfs", "report": "100-gfs.json"},
        ],
    )

    assert summary["passed"] is False
    assert summary["failed_at"] == {"points": 50, "scope": "cams", "report": "50-cams.json"}
    assert summary["skipped"] == [{"points": 100, "scope": "gfs", "report": "100-gfs.json"}]
