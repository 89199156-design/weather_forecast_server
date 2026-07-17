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

            left = build_identity(shanghai)
            right = build_identity(singapore)
            left["live_snapshot"] = {"marker_matches_live_snapshot": True}
            right["live_snapshot"] = {"marker_matches_live_snapshot": True}
            report = compare_identities(left, right)

            self.assertTrue(report["passed"])
            self.assertEqual(report["matched_latest_runs"]["gfs"], "2026071300")

    def test_different_gfs_run_fails_before_data_comparison(self):
        left = {"groups": {"gfs": {"latest_complete_run": "2026071300"}, "cams": {"latest_complete_run": "2026071212"}}}
        right = {"groups": {"gfs": {"latest_complete_run": "2026071306"}, "cams": {"latest_complete_run": "2026071212"}}}

        report = compare_identities(left, right)

        self.assertFalse(report["passed"])
        self.assertTrue(any(item.get("group") == "gfs" for item in report["mismatches"]))

    def test_matching_marker_names_without_live_snapshot_fail_closed(self):
        identity = {
            "groups": {
                "gfs": {"latest_complete_run": "2026071300"},
                "cams": {"latest_complete_run": "2026071212"},
            }
        }

        report = compare_identities(identity, identity)

        self.assertFalse(report["passed"])
        self.assertFalse(report["live_snapshot_verified"])
        self.assertEqual(
            [item["endpoint"] for item in report["mismatches"][:2]],
            ["shanghai", "singapore"],
        )


if __name__ == "__main__":
    unittest.main()
