import json
import tempfile
import unittest
from pathlib import Path

from scripts.compare_model_run_identities import build_identity, compare_identities


def write_marker(root: Path, group: str, run: str, native: bool) -> None:
    path = root / "groups" / group / "current/ready_for_processing.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "complete",
        "group": group,
        "latest_complete_run": run,
        "products": {f"{group}_product": {"coverage_id": "native"}}
        if native
        else [f"{group}_product"],
    }
    if native:
        payload["source_runs"] = [run]
    path.write_text(json.dumps(payload), encoding="utf-8")


class ModelRunIdentityComparisonTests(unittest.TestCase):
    def test_native_and_legacy_markers_match_by_latest_model_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shanghai = root / "shanghai"
            singapore = root / "singapore"
            for group, run in (("gfs", "2026071300"), ("cams", "2026071212")):
                write_marker(shanghai, group, run, native=False)
                write_marker(singapore, group, run, native=True)

            report = compare_identities(build_identity(shanghai), build_identity(singapore))

            self.assertTrue(report["passed"])
            self.assertEqual(report["matched_latest_runs"]["gfs"], "2026071300")

    def test_different_gfs_run_fails_before_data_comparison(self):
        left = {"groups": {"gfs": {"latest_complete_run": "2026071300"}, "cams": {"latest_complete_run": "2026071212"}}}
        right = {"groups": {"gfs": {"latest_complete_run": "2026071306"}, "cams": {"latest_complete_run": "2026071212"}}}

        report = compare_identities(left, right)

        self.assertFalse(report["passed"])
        self.assertEqual(report["mismatches"][0]["group"], "gfs")


if __name__ == "__main__":
    unittest.main()
