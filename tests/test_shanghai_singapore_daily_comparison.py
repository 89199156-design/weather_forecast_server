import importlib.util
import json
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "compare_shanghai_singapore_daily.py"
spec = importlib.util.spec_from_file_location("compare_daily", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ShanghaiSingaporeDailyComparisonTests(unittest.TestCase):
    def test_contract_is_three_days_for_all_supported_daily_variables(self):
        self.assertEqual(module.daily_start_date("2026071312").isoformat(), "2026-07-14")
        self.assertEqual(len(module.GFS_DAILY), 61)
        self.assertEqual(len(module.CAMS_DAILY), 11)
        self.assertEqual(2000 * 3 * (61 + 11), 432_000)
        self.assertEqual(module.CAMS_DAILY_STRICT, {"chinese_aqi_pm2_5", "chinese_aqi_pm10"})
        self.assertEqual(len(module.CAMS_DAILY_EXPECTED_SEMANTIC_DIFFERENCE_VARIABLES), 9)
        self.assertEqual(2000 * 3 * (61 + 2), 378_000)
        self.assertEqual(2000 * 3 * 9, 54_000)

    def test_request_uses_china_timezone_and_exact_date_window(self):
        path = module.request_path(
            "gfs", [{"latitude": 31.2, "longitude": 121.5}],
            ["temperature_2m_max"], "2026071312", 3,
            start_date=date(2026, 7, 15),
        )
        self.assertIn("timezone=Asia%2FShanghai", path)
        self.assertIn("start_date=2026-07-15", path)
        self.assertIn("end_date=2026-07-17", path)

    def test_shared_18z_starts_at_next_complete_shanghai_day(self):
        start, end = module.derive_complete_daily_window(
            datetime(2026, 7, 13, 18),
            datetime(2026, 7, 17, 15),
            3,
        )
        self.assertEqual(start.isoformat(), "2026-07-15")
        self.assertEqual(end.isoformat(), "2026-07-17")

    def test_shared_local_midnight_keeps_that_shanghai_day(self):
        start, end = module.derive_complete_daily_window(
            datetime(2026, 7, 13, 16),
            datetime(2026, 7, 16, 15),
            3,
        )
        self.assertEqual(start.isoformat(), "2026-07-14")
        self.assertEqual(end.isoformat(), "2026-07-16")

    def test_shared_window_one_hour_short_rejects_three_days(self):
        with self.assertRaisesRegex(ValueError, "cannot cover 3 complete Asia/Shanghai days"):
            module.derive_complete_daily_window(
                datetime(2026, 7, 13, 18),
                datetime(2026, 7, 17, 14),
                3,
            )

    def test_hourly_report_is_validated_and_recorded_as_window_source(self):
        report = {
            "passed": True,
            "gfs_run": "2026071406",
            "shared_gfs_window": {
                "reason": "actual_shared_window",
                "run": "2026071406",
                "shared_start": "2026-07-13T18:00",
                "shared_end": "2026-07-17T15:00",
                "shared_hours": 94,
            },
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            start, end, source = module.load_daily_window_from_hourly_report(
                path, "2026071406", 3
            )

        self.assertEqual(start, date(2026, 7, 15))
        self.assertEqual(end, date(2026, 7, 17))
        self.assertEqual(source["type"], "hourly_acceptance_report_shared_gfs_window")
        self.assertEqual(source["shared_start"], "2026-07-13T18:00")
        self.assertEqual(source["shared_end"], "2026-07-17T15:00")

    def test_failed_hourly_acceptance_report_is_rejected(self):
        report = {
            "passed": False,
            "gfs_run": "2026071406",
            "shared_gfs_window": {
                "reason": "actual_shared_window",
                "run": "2026071406",
                "shared_start": "2026-07-13T18:00",
                "shared_end": "2026-07-17T15:00",
                "shared_hours": 94,
            },
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "passed must be true"):
                module.load_daily_window_from_hourly_report(path, "2026071406", 3)

    def test_shared_window_for_another_gfs_run_is_rejected(self):
        report = {
            "passed": True,
            "gfs_run": "2026071406",
            "shared_gfs_window": {
                "reason": "actual_shared_window",
                "run": "2026071400",
                "shared_start": "2026-07-13T18:00",
                "shared_end": "2026-07-17T15:00",
                "shared_hours": 94,
            },
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "shared_gfs_window run"):
                module.load_daily_window_from_hourly_report(path, "2026071406", 3)

    def test_daily_diagnostics_preserve_numeric_type(self):
        left = {
            "generationtime_ms": 1.0, "latitude": 31.0,
            "daily_units": {"time": "iso8601", "v": "m"},
            "daily": {"time": ["a"], "v": [1.0]},
        }
        right = {
            "generationtime_ms": 2.0, "latitude": 31,
            "daily_units": {"time": "iso8601", "v": "cm"},
            "daily": {"time": ["a"], "v": [1]},
        }
        summary = module.daily_mismatch_summary(left, right, ["v"])
        self.assertEqual(summary["counts"]["metadata"], {"latitude": 1})
        self.assertEqual(summary["counts"]["daily_units"], {"v": 1})
        self.assertEqual(summary["counts"]["daily_values"], {"v": 1})

    def test_daily_waiver_is_observed_without_hiding_strict_pm_mismatches(self):
        shanghai = {
            "latitude": 31.2,
            "longitude": 121.5,
            "daily_units": {
                "time": "iso8601",
                "chinese_aqi_pm2_5": "Chinese AQI",
                "chinese_aqi_o3": "Chinese AQI",
            },
            "daily": {
                "time": ["2026-07-14", "2026-07-15", "2026-07-16"],
                "chinese_aqi_pm2_5": [10.0, 11.0, 12.0],
                "chinese_aqi_o3": [20.0, 21.0, 22.0],
            },
        }
        singapore = json.loads(json.dumps(shanghai))
        singapore["daily"]["chinese_aqi_o3"] = [200.0, 210.0, 220.0]
        job = {
            "job_id": "cams-p0000-v000",
            "scope": "cams",
            "points": [{"latitude": 31.2, "longitude": 121.5}],
            "variables": ["chinese_aqi_pm2_5", "chinese_aqi_o3"],
            "gfs_run": "2026071312",
            "days": 3,
        }

        with patch.object(module, "fetch", side_effect=[shanghai, singapore]):
            result = module.compare_job(job, "http://shanghai", "http://singapore", 1.0, 0.0)

        self.assertTrue(result["equal"])
        self.assertEqual(result["values"], 3)
        self.assertEqual(result["semantic_waiver_values"], 3)
        self.assertEqual(result["semantic_waiver_mismatches"], 3)
        self.assertEqual(
            result["expected_semantic_differences"]["by_variable"]["chinese_aqi_o3"]["mismatched_values"],
            3,
        )

        strict_mismatch = json.loads(json.dumps(singapore))
        strict_mismatch["daily"]["chinese_aqi_pm2_5"][1] = 99.0
        with patch.object(module, "fetch", side_effect=[shanghai, strict_mismatch]):
            failed = module.compare_job(job, "http://shanghai", "http://singapore", 1.0, 0.0)

        self.assertFalse(failed["equal"])
        self.assertEqual(
            failed["field_mismatches"]["counts"]["daily_values"],
            {"chinese_aqi_pm2_5": 1},
        )


if __name__ == "__main__":
    unittest.main()
