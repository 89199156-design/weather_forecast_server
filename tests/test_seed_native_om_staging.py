import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from seed_native_om_staging import seed_staging


class SeedNativeOmStagingTests(unittest.TestCase):
    def test_hardlinks_safe_older_coverage_and_reports_reused_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = root / "coverages" / "gfs" / "gfs_native_2026071300"
            data = coverage / "ncep_gfs013" / "temperature_2m" / "chunk_1.om"
            data.parent.mkdir(parents=True)
            data.write_bytes(b"native")
            marker = {
                "status": "complete",
                "runtime_format": "openmeteo-native-v1",
                "latest_complete_run": "2026071300",
                "source_runs": ["2026071200", "2026071206", "2026071212", "2026071218", "2026071300"],
                "coverage_path": "coverages/gfs/gfs_native_2026071300",
                "files": 1,
                "bytes": 6,
            }
            marker_path = root / "groups" / "gfs" / "current" / "ready_for_processing.json"
            marker_path.parent.mkdir(parents=True)
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            (root / "current").mkdir()
            (root / "current" / "gfs").symlink_to(Path("..") / "coverages" / "gfs" / coverage.name)
            staging = root / "staging" / "gfs_test"

            result = seed_staging(
                root,
                staging,
                "gfs",
                ["2026071206", "2026071212", "2026071218", "2026071300", "2026071306"],
            )

            staged_data = staging / data.relative_to(coverage)
            self.assertEqual(result["reused_source_runs"], ["2026071206", "2026071212", "2026071218", "2026071300"])
            self.assertEqual(result["seeded_latest_complete_run"], "2026071300")
            self.assertEqual(os.stat(data).st_ino, os.stat(staged_data).st_ino)

    def test_does_not_seed_newer_coverage_into_older_backfill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = root / "coverages" / "cams" / "cams_native_2026071312"
            coverage.mkdir(parents=True)
            marker = {
                "status": "complete",
                "runtime_format": "openmeteo-native-v1",
                "latest_complete_run": "2026071312",
                "source_runs": ["2026071212", "2026071300", "2026071312"],
                "coverage_path": "coverages/cams/cams_native_2026071312",
                "files": 0,
                "bytes": 0,
            }
            marker_path = root / "groups" / "cams" / "current" / "ready_for_processing.json"
            marker_path.parent.mkdir(parents=True)
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            (root / "current").mkdir()
            (root / "current" / "cams").symlink_to(Path("..") / "coverages" / "cams" / coverage.name)
            staging = root / "staging" / "cams_test"

            result = seed_staging(root, staging, "cams", ["2026071200", "2026071212", "2026071300"])

            self.assertIsNone(result["seeded_from"])
            self.assertIsNone(result["seeded_latest_complete_run"])
            self.assertTrue(staging.is_dir())

    def test_missing_om_file_forces_full_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = root / "coverages" / "gfs" / "gfs_native_2026071300"
            coverage.mkdir(parents=True)
            marker = {
                "status": "complete",
                "runtime_format": "openmeteo-native-v1",
                "latest_complete_run": "2026071300",
                "source_runs": ["2026071200", "2026071206", "2026071212", "2026071218", "2026071300"],
                "coverage_path": "coverages/gfs/gfs_native_2026071300",
                "files": 10,
                "bytes": 1000,
            }
            marker_path = root / "groups" / "gfs" / "current" / "ready_for_processing.json"
            marker_path.parent.mkdir(parents=True)
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            (root / "current").mkdir()
            (root / "current" / "gfs").symlink_to(Path("..") / "coverages" / "gfs" / coverage.name)
            staging = root / "staging" / "gfs_rebuild"

            result = seed_staging(
                root,
                staging,
                "gfs",
                ["2026071206", "2026071212", "2026071218", "2026071300", "2026071306"],
            )

            self.assertIsNone(result["seeded_from"])
            self.assertEqual(result["seed_rejected_reason"], "coverage_size_mismatch")
            self.assertEqual(list(staging.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
