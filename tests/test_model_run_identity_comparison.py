import json
import tempfile
import unittest
from pathlib import Path

from scripts.compare_model_run_identities import (
    build_identity,
    compare_identities,
    snapshot_identity_matches,
)


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
    def test_native_snapshot_requires_its_immutable_coverage_id(self):
        self.assertEqual(
            snapshot_identity_matches(
                ["gfs_native_2026071812_v1"],
                ["gfs_native_2026071812_v1"],
                "2026071812",
                {"2026071812"},
            ),
            (True, "coverage_id"),
        )
        self.assertEqual(
            snapshot_identity_matches(
                ["gfs_native_2026071812_v1"],
                [],
                "2026071812",
                {"2026071812"},
            ),
            (False, "coverage_id"),
        )

    def test_official_bucket_snapshot_uses_the_loaded_model_run(self):
        self.assertEqual(
            snapshot_identity_matches([], [], "2026071812", {"2026071812"}),
            (True, "source_run"),
        )
        self.assertEqual(
            snapshot_identity_matches([], [], "2026071812", {"2026071806"}),
            (False, "source_run"),
        )

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

    def test_official_bucket_latest_marker_layout_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for group, run in (("gfs", "2026071806"), ("cams", "2026071712")):
                path = root / "groups" / group / "latest.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "status": "complete",
                            "group": group,
                            "latest_complete_run": run,
                            "products": [f"{group}_product"],
                        }
                    ),
                    encoding="utf-8",
                )

            identity = build_identity(root)

            self.assertEqual(identity["groups"]["gfs"]["latest_complete_run"], "2026071806")
            self.assertEqual(identity["groups"]["cams"]["latest_complete_run"], "2026071712")
            self.assertEqual(identity["groups"]["gfs"]["source_runs"], [])

    def test_inventory_includes_independent_greenhouse_when_published(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_marker(root, "gfs", "2026072018", native=True)
            write_marker(root, "cams", "2026072012", native=True)
            write_marker(root, "cams_greenhouse", "2026072000", native=True)

            identity = build_identity(root)

            self.assertEqual(
                identity["groups"]["cams_greenhouse"]["latest_complete_run"],
                "2026072000",
            )

    def test_comparison_uses_main_model_runs_not_optional_greenhouse_cycle(self):
        left = {
            "groups": {
                "gfs": {"latest_complete_run": "2026072018"},
                "cams": {"latest_complete_run": "2026072012"},
            },
            "live_snapshot": {"marker_matches_live_snapshot": True},
        }
        right = {
            "groups": {
                **left["groups"],
                "cams_greenhouse": {"latest_complete_run": "2026072000"},
            },
            "live_snapshot": {"marker_matches_live_snapshot": True},
        }

        report = compare_identities(left, right)

        self.assertTrue(report["passed"])

    def test_matching_latest_run_allows_different_internal_history_vectors(self):
        left = {
            "groups": {
                "gfs": {
                    "latest_complete_run": "2026071300",
                    "source_runs": ["2026071212", "2026071300"],
                },
                "cams": {
                    "latest_complete_run": "2026071212",
                    "source_runs": ["2026071200", "2026071212"],
                },
            }
        }
        right = {
            "groups": {
                "gfs": {
                    "latest_complete_run": "2026071300",
                    "source_runs": ["2026071218", "2026071300"],
                },
                "cams": {
                    "latest_complete_run": "2026071212",
                    "source_runs": ["2026071200", "2026071212"],
                },
            }
        }
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
