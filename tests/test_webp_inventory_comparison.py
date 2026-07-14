import json
import tempfile
import unittest
from pathlib import Path

from scripts.compare_webp_inventories import build_inventory, compare_inventories


def make_scope(root: Path, scope: str, run: str, content: bytes) -> None:
    product = "gfs013_surface" if scope == "gfs" else "cams_global"
    manifest_name = f"{product}_data.json"
    release = root / "releases" / f"{scope}-release"
    product_root = release / product
    layer = product_root / "layer"
    layer.mkdir(parents=True)
    (layer / "frame.webp").write_bytes(content)
    (product_root / manifest_name).write_text(
        json.dumps(
            {
                "generated_at": 123,
                "source": scope,
                "source_release_id": f"{scope}-source",
                "source_run": run,
                "batch": 1,
                "frame_count": 1,
                "frame_step_seconds": 3600,
                "file_pattern": "{timestamp}_{batch}.webp",
                "files": [1],
                "grid": {"width": 1, "height": 1},
                "layers": {"layer": {"encoding": "scalar"}},
            }
        ),
        encoding="utf-8",
    )
    marker = root / "current" / f"{scope}.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "status": "complete",
                "scope": scope,
                "release_id": f"{scope}-source",
                "run": run,
                "path": str(release),
            }
        ),
        encoding="utf-8",
    )


class WebpInventoryComparisonTests(unittest.TestCase):
    def test_identical_webp_bytes_pass_despite_dynamic_manifest_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shanghai = root / "shanghai"
            singapore = root / "singapore"
            for target in (shanghai, singapore):
                make_scope(target, "gfs", "2026071300", b"same-gfs")
                make_scope(target, "cams", "2026071300", b"same-cams")
            manifest_path = singapore / "releases/gfs-release/gfs013_surface/gfs013_surface_data.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["generated_at"] = 999
            manifest["source_release_id"] = "different-native-release-id"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            payload_left = build_inventory(shanghai, strict=False)
            payload_right = build_inventory(singapore, strict=False)

            report = compare_inventories(payload_left, payload_right)

            self.assertTrue(report["passed"])
            self.assertTrue(report["exact_webp_bytes"])

    def test_one_changed_webp_byte_fails_with_file_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shanghai = root / "shanghai"
            singapore = root / "singapore"
            for scope in ("gfs", "cams"):
                make_scope(shanghai, scope, "2026071300", b"same")
                make_scope(singapore, scope, "2026071300", b"same")
            (singapore / "releases/gfs-release/gfs013_surface/layer/frame.webp").write_bytes(b"changed")

            report = compare_inventories(
                build_inventory(shanghai, strict=False),
                build_inventory(singapore, strict=False),
            )

            self.assertFalse(report["passed"])
            mismatch = next(item for item in report["mismatches"] if item["reason"] == "webp_sha256_mismatch")
            self.assertEqual(mismatch["path"], "layer/frame.webp")


if __name__ == "__main__":
    unittest.main()
