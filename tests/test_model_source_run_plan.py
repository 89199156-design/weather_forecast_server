from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_source_run_plan import plan_source_runs


class ModelSourceRunPlanTests(unittest.TestCase):
    def test_gfs_keeps_four_six_hour_history_runs_and_latest_full_run(self):
        plan = plan_source_runs(
            "2026071306",
            cadence_hours=6,
            source_run_count=5,
            historical_max_forecast_hour=5,
            latest_max_forecast_hour=384,
            local_utc_offset_hours=8,
        )

        self.assertEqual(
            plan.source_runs,
            ("2026071206", "2026071212", "2026071218", "2026071300", "2026071306"),
        )
        self.assertEqual(plan.public_start_utc, "2026-07-12T06:00:00Z")
        self.assertEqual(plan.local_day_start_utc, "2026-07-12T16:00:00Z")
        self.assertEqual(plan.public_end_utc, "2026-07-29T06:00:00Z")
        self.assertEqual(plan.public_hours, 408)

    def test_five_gfs_runs_cover_utc8_midnight_for_every_cycle(self):
        for hour in (0, 6, 12, 18):
            plan = plan_source_runs(
                f"20260713{hour:02d}",
                cadence_hours=6,
                source_run_count=5,
                historical_max_forecast_hour=5,
                latest_max_forecast_hour=384,
                local_utc_offset_hours=8,
            )
            self.assertEqual(plan.public_hours, 408)

    def test_cams_keeps_three_complete_twelve_hour_runs(self):
        plan = plan_source_runs(
            "2026071312",
            cadence_hours=12,
            source_run_count=3,
            historical_max_forecast_hour=120,
            latest_max_forecast_hour=120,
            local_utc_offset_hours=8,
        )

        self.assertEqual(plan.source_runs, ("2026071212", "2026071300", "2026071312"))
        self.assertEqual(plan.public_start_utc, "2026-07-12T12:00:00Z")
        self.assertEqual(plan.public_hours, 144)

    def test_rejects_history_that_does_not_bridge_cadence(self):
        with self.assertRaisesRegex(ValueError, "at least one run cadence"):
            plan_source_runs(
                "2026071300",
                cadence_hours=6,
                source_run_count=5,
                historical_max_forecast_hour=4,
                latest_max_forecast_hour=384,
                local_utc_offset_hours=8,
            )


if __name__ == "__main__":
    unittest.main()
