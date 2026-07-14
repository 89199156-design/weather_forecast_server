from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def official_times(run: datetime, max_hour: int) -> list[str]:
    hours = list(range(min(max_hour, 120) + 1))
    if max_hour > 120:
        hours.extend(range(123, max_hour + 1, 3))
    return [
        (run + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%MZ")
        for hour in hours
    ]


class GfsHorizonValidationTests(unittest.TestCase):
    def run_validator(self, data_dir: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "validate_openmeteo_latest_run.py"),
                "--data-dir",
                str(data_dir),
                "--run",
                "2026071300",
                "--domains",
                "ncep_gfs013,ncep_gfs025",
                "--min-frames",
                "0",
                "--gfs-max-forecast-hour",
                "384",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def write_latest(self, data_dir: Path, domain: str, valid_times: list[str]) -> None:
        path = data_dir / "data_run" / domain / "latest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "reference_time": "2026-07-13T00:00:00Z",
                    "valid_times": valid_times,
                }
            ),
            encoding="utf-8",
        )

    def test_accepts_complete_384_hour_sparse_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            times = official_times(datetime(2026, 7, 13, tzinfo=UTC), 384)
            self.write_latest(data_dir, "ncep_gfs013", times)
            self.write_latest(data_dir, "ncep_gfs025", times)

            result = self.run_validator(data_dir)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_missing_final_384_hour_step(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            times = official_times(datetime(2026, 7, 13, tzinfo=UTC), 384)[:-1]
            self.write_latest(data_dir, "ncep_gfs013", times)
            self.write_latest(data_dir, "ncep_gfs025", times)

            result = self.run_validator(data_dir)

            self.assertEqual(result.returncode, 1)
            self.assertIn("first_missing=2026-07-29T00:00:00Z", result.stderr)


if __name__ == "__main__":
    unittest.main()
