import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "probe_cams_ftp_run.py"


def load_module():
    spec = importlib.util.spec_from_file_location("probe_cams_ftp_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cams_probe_checks_all_hourly_forecast_files_by_default():
    probe = load_module()
    run = datetime(2026, 7, 3, 0, tzinfo=timezone.utc)
    variables = list(probe.CAMS_GLOBAL_META)

    urls = probe.cams_urls(
        run=run,
        variables=variables,
        forecast_hours=probe.probe_forecast_hours(None, max_forecast_hour=120),
    )

    assert len(urls) == len(variables) * 121
    assert any("_fc_sfc_001_pm2p5.nc" in url for url in urls)
    assert any("_fc_ml137_120_no2.nc" in url for url in urls)
    assert any("_fc_sfc_002_pm2p5.nc" in url for url in urls)


def test_cams_probe_forecast_hours_are_sorted_unique_and_bounded():
    probe = load_module()

    assert probe.probe_forecast_hours("120,0,1,1", max_forecast_hour=120) == [0, 1, 120]
    assert probe.probe_forecast_hours(None, max_forecast_hour=24) == list(range(25))


def test_cams_probe_stops_scheduling_urls_after_first_failure(monkeypatch):
    probe = load_module()
    run = datetime(2026, 7, 3, 0, tzinfo=timezone.utc)
    calls = []

    def fake_check_url(url, authorization, timeout_seconds):
        calls.append(url)
        return probe.ProbeResult(url=url, ok=False, detail="http_429")

    monkeypatch.setattr(probe, "check_url", fake_check_url)

    complete, failures = probe.run_complete(
        run=run,
        variables=["pm2_5"],
        forecast_hours=list(range(121)),
        timeout_seconds=1,
        workers=4,
        authorization="Basic test",
    )

    assert complete is False
    assert failures[0].detail == "http_429"
    assert len(calls) <= 4
