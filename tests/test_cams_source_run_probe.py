from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_probe_module():
    path = ROOT / "scripts" / "probe_cams_ftp_run.py"
    spec = importlib.util.spec_from_file_location("probe_cams_source_runs", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CamsSourceRunProbeTests(unittest.TestCase):
    def test_default_remote_probe_uses_only_first_and_final_sentinels(self):
        probe = load_probe_module()

        self.assertEqual(probe.probe_forecast_hours("0,120", 120), [0, 120])

    def test_rechecks_same_latest_when_one_of_three_runs_is_missing(self):
        probe = load_probe_module()
        latest = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)

        candidates = probe.candidate_runs(
            latest,
            latest,
            72,
            {"2026071300", "2026071312"},
        )

        self.assertEqual(candidates, [latest])

    def test_skips_same_latest_when_all_three_runs_exist(self):
        probe = load_probe_module()
        latest = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)

        candidates = probe.candidate_runs(
            latest,
            latest,
            72,
            {"2026071212", "2026071300", "2026071312"},
        )

        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
