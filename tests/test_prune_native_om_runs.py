from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from prune_native_om_runs import prune_native_runs, run_relative_path


class PruneNativeOmRunsTests(unittest.TestCase):
    def test_keeps_exact_five_gfs_runs_and_removes_raw_data(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "staging"
            retained = ["2026071206", "2026071212", "2026071218", "2026071300", "2026071306"]
            for domain in ("ncep_gfs013", "ncep_gfs025"):
                for run in ["2026071200", *retained]:
                    run_dir = data / "data_run" / domain / run_relative_path(run)
                    run_dir.mkdir(parents=True)
                    (run_dir / "temperature.om").write_bytes(b"om")
                (data / "data_run" / domain / "latest.json").write_text("{}", encoding="utf-8")
            (data / "download-ncep_gfs013").mkdir(parents=True)
            (data / "http_cache" / "gfs").mkdir(parents=True)

            result = prune_native_runs(data, ["ncep_gfs013", "ncep_gfs025"], retained)

            for domain in ("ncep_gfs013", "ncep_gfs025"):
                self.assertFalse((data / "data_run" / domain / run_relative_path("2026071200")).exists())
                for run in retained:
                    self.assertTrue((data / "data_run" / domain / run_relative_path(run)).is_dir())
            self.assertFalse((data / "download-ncep_gfs013").exists())
            self.assertFalse((data / "http_cache").exists())
            self.assertEqual(result["removed_transient"], ["download-ncep_gfs013", "http_cache"])

    def test_refuses_to_publish_when_a_retained_run_directory_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "staging"
            (data / "data_run" / "cams_global").mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, "missing retained run directories"):
                prune_native_runs(
                    data,
                    ["cams_global"],
                    ["2026071212", "2026071300", "2026071312"],
                )


if __name__ == "__main__":
    unittest.main()
