import json
import tempfile
import unittest
from pathlib import Path

from scripts.compare_webp_inventories import build_inventory, compare_inventories


def make_scope(
    root: Path,
    scope: str,
    run: str,
    content: bytes,
    *,
    layer_name: str = "layer",
    forecast_hour: int = 0,
) -> None:
    product = "gfs013_surface" if scope == "gfs" else "cams_global"
    manifest_name = f"{product}_data.json"
    release = root / "releases" / f"{scope}-release"
    product_root = release / product
    layer = product_root / layer_name
    layer.mkdir(parents=True)
    run_epoch = 1_784_246_400
    frame_name = f"{run_epoch + forecast_hour * 3600}_{run_epoch}.webp"
    (layer / frame_name).write_bytes(content)
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
                "layers": {layer_name: {"encoding": "scalar"}},
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
            self.assertEqual(report["excluded_semantic_frame_count"], 0)

    def test_one_changed_webp_byte_fails_with_file_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shanghai = root / "shanghai"
            singapore = root / "singapore"
            for scope in ("gfs", "cams"):
                make_scope(shanghai, scope, "2026071300", b"same")
                make_scope(singapore, scope, "2026071300", b"same")
            frame = next(
                (singapore / "releases/gfs-release/gfs013_surface/layer").glob("*.webp")
            )
            frame.write_bytes(b"changed")

            report = compare_inventories(
                build_inventory(shanghai, strict=False),
                build_inventory(singapore, strict=False),
            )

            self.assertFalse(report["passed"])
            mismatch = next(item for item in report["mismatches"] if item["reason"] == "webp_sha256_mismatch")
            self.assertTrue(mismatch["path"].startswith("layer/"))

    def test_cams_dust_interpolated_hours_are_excluded_but_direct_hours_are_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shanghai = root / "shanghai"
            singapore = root / "singapore"
            make_scope(
                shanghai,
                "gfs",
                "2026071700",
                b"same-gfs",
            )
            make_scope(
                singapore,
                "gfs",
                "2026071700",
                b"same-gfs",
            )
            make_scope(
                shanghai,
                "cams",
                "2026071700",
                b"shanghai-interpolated",
                layer_name="dust",
                forecast_hour=1,
            )
            make_scope(
                singapore,
                "cams",
                "2026071700",
                b"singapore-direct",
                layer_name="dust",
                forecast_hour=1,
            )

            report = compare_inventories(
                build_inventory(shanghai, strict=False),
                build_inventory(singapore, strict=False),
            )

            self.assertTrue(report["passed"])
            self.assertFalse(report["exact_webp_bytes"])
            self.assertTrue(report["strict_comparable_webp_bytes"])
            self.assertEqual(report["excluded_semantic_frame_count"], 1)

            direct_frame = next(
                (singapore / "releases/cams-release/cams_global/dust").glob("*.webp")
            )
            direct_name = direct_frame.name.replace(
                str(1_784_246_400 + 3600),
                str(1_784_246_400),
            )
            direct_frame.rename(direct_frame.with_name(direct_name))
            shanghai_frame = next(
                (shanghai / "releases/cams-release/cams_global/dust").glob("*.webp")
            )
            shanghai_frame.rename(shanghai_frame.with_name(direct_name))

            direct_report = compare_inventories(
                build_inventory(shanghai, strict=False),
                build_inventory(singapore, strict=False),
            )
            self.assertFalse(direct_report["passed"])
            self.assertEqual(direct_report["excluded_semantic_frame_count"], 0)


if __name__ == "__main__":
    unittest.main()
