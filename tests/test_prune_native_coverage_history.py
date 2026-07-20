import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "prune_native_coverage_history.py"
SPEC = importlib.util.spec_from_file_location("prune_native_coverage_history", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def coverage_payload(scope: str, coverage_id: str) -> dict[str, object]:
    return {
        "status": "complete",
        "runtime_format": "openmeteo-native-v1",
        "group": scope,
        "coverage_id": coverage_id,
    }


def release_payload(scope: str, coverage_id: str) -> dict[str, object]:
    return {
        **coverage_payload(scope, coverage_id),
        "release_id": coverage_id,
        "coverage_path": f"coverages/{scope}/{coverage_id}",
    }


def prepare_root(root: Path, scope: str, current_id: str, old_id: str) -> None:
    for coverage_id in (current_id, old_id):
        write_json(
            root / "coverages" / scope / coverage_id / "coverage.json",
            coverage_payload(scope, coverage_id),
        )
        write_json(
            root / "groups" / scope / "releases" / f"{coverage_id}.json",
            release_payload(scope, coverage_id),
        )
    current_link = root / "current" / scope
    current_link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(
        Path("..") / "coverages" / scope / current_id,
        current_link,
        target_is_directory=True,
    )
    write_json(
        root / "groups" / scope / "current" / "ready_for_processing.json",
        release_payload(scope, current_id),
    )


class PruneNativeCoverageHistoryTests(unittest.TestCase):
    def test_removes_only_non_current_coverage_and_release_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_id = "gfs_native_2026071800"
            old_id = "gfs_native_2026071718"
            prepare_root(root, "gfs", current_id, old_id)

            result = MODULE.prune_coverage_history(root, "gfs", current_id)

            self.assertEqual(result["removed_coverages"], [old_id])
            self.assertEqual(result["removed_release_markers"], [f"{old_id}.json"])
            self.assertTrue((root / "coverages" / "gfs" / current_id).is_dir())
            self.assertFalse((root / "coverages" / "gfs" / old_id).exists())
            self.assertTrue(
                (root / "groups" / "gfs" / "releases" / f"{current_id}.json").is_file()
            )
            self.assertFalse(
                (root / "groups" / "gfs" / "releases" / f"{old_id}.json").exists()
            )

    def test_expected_identity_mismatch_retains_all_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_id = "cams_native_2026071800"
            old_id = "cams_native_2026071712"
            prepare_root(root, "cams", current_id, old_id)

            with self.assertRaisesRegex(ValueError, "current coverage changed"):
                MODULE.prune_coverage_history(root, "cams", old_id)

            self.assertTrue((root / "coverages" / "cams" / current_id).is_dir())
            self.assertTrue((root / "coverages" / "cams" / old_id).is_dir())

    def test_greenhouse_prunes_only_after_exact_current_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_id = "cams_greenhouse_native_2026072000_independent-v1"
            old_id = "cams_greenhouse_native_2026071900_independent-v1"
            prepare_root(root, "cams_greenhouse", current_id, old_id)

            result = MODULE.prune_coverage_history(
                root,
                "cams_greenhouse",
                current_id,
            )

            self.assertEqual(result["removed_coverages"], [old_id])
            self.assertTrue(
                (root / "coverages" / "cams_greenhouse" / current_id).is_dir()
            )

    def test_traversal_marker_is_rejected_before_any_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_id = "gfs_native_2026071800"
            old_id = "gfs_native_2026071718"
            prepare_root(root, "gfs", current_id, old_id)
            marker_path = (
                root / "groups" / "gfs" / "current" / "ready_for_processing.json"
            )
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker["coverage_path"] = "../outside"
            write_json(marker_path, marker)

            with self.assertRaisesRegex(ValueError, "unsafe current coverage_path"):
                MODULE.prune_coverage_history(root, "gfs", current_id)

            self.assertTrue((root / "coverages" / "gfs" / current_id).is_dir())
            self.assertTrue((root / "coverages" / "gfs" / old_id).is_dir())


if __name__ == "__main__":
    unittest.main()
