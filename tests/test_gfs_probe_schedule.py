from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_probe_module():
    path = ROOT / "scripts" / "probe_gfs_official_run.py"
    scripts_dir = str(path.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("probe_gfs_official_run", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GfsProbeScheduleTests(unittest.TestCase):
    def test_gfs_forecast_schedule_is_hourly_through_120(self):
        probe = load_probe_module()

        self.assertEqual(probe.gfs_forecast_hours(120), list(range(121)))

    def test_gfs_forecast_schedule_is_three_hourly_after_120(self):
        probe = load_probe_module()

        hours = probe.gfs_forecast_hours(384)

        self.assertEqual(len(hours), 209)
        self.assertEqual(hours[:121], list(range(121)))
        self.assertEqual(hours[121:125], [123, 126, 129, 132])
        self.assertEqual(hours[-1], 384)
        self.assertNotIn(121, hours)
        self.assertNotIn(122, hours)
        self.assertNotIn(124, hours)

    def test_gfs_probe_checks_only_boundary_sentinels_for_all_three_products(self):
        probe = load_probe_module()
        run = datetime(2026, 7, 13, 0, tzinfo=timezone.utc)

        urls = probe.gfs_urls(run, 384)

        self.assertEqual(len(urls), 5 * 3)
        self.assertTrue(any("sfluxgrbf000.grib2.idx" in url for url in urls))
        self.assertTrue(any("pgrb2.0p25.f005.idx" in url for url in urls))
        self.assertTrue(any("sfluxgrbf120.grib2.idx" in url for url in urls))
        self.assertTrue(any("pgrb2.0p25.f123.idx" in url for url in urls))
        self.assertTrue(any("pgrb2b.0p25.f384.idx" in url for url in urls))
        self.assertFalse(any("f121" in url for url in urls))
        self.assertFalse(any("f126" in url for url in urls))

    def test_gfs_forecast_schedule_rejects_negative_horizon(self):
        probe = load_probe_module()

        with self.assertRaisesRegex(ValueError, "must not be negative"):
            probe.gfs_forecast_hours(-1)

        with self.assertRaisesRegex(ValueError, "must not exceed"):
            probe.gfs_forecast_hours(385)

    def test_probe_reads_latest_run_from_native_producer_marker(self):
        probe = load_probe_module()
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            marker = data_dir / "groups" / "gfs" / "current" / "ready_for_processing.json"
            marker.parent.mkdir(parents=True)
            marker.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "latest_complete_run": "2026071306",
                        "source_runs": [
                            "2026071206",
                            "2026071212",
                            "2026071218",
                            "2026071300",
                            "2026071306",
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                probe.read_local_latest(data_dir),
                datetime(2026, 7, 13, 6, tzinfo=timezone.utc),
            )

    def test_probe_rechecks_same_latest_run_when_history_batch_is_missing(self):
        probe = load_probe_module()
        latest = datetime(2026, 7, 13, 6, tzinfo=timezone.utc)

        candidates = probe.candidate_runs(
            latest,
            latest,
            36,
            {"2026071212", "2026071218", "2026071300", "2026071306"},
        )

        self.assertEqual(candidates, [latest])

    def test_probe_skips_same_latest_run_when_all_five_batches_exist(self):
        probe = load_probe_module()
        latest = datetime(2026, 7, 13, 6, tzinfo=timezone.utc)

        candidates = probe.candidate_runs(
            latest,
            latest,
            36,
            {"2026071206", "2026071212", "2026071218", "2026071300", "2026071306"},
        )

        self.assertEqual(candidates, [])

if __name__ == "__main__":
    unittest.main()
