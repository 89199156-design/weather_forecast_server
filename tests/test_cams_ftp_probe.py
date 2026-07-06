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
