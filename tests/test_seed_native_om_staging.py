from datetime import datetime
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from seed_native_om_staging import (
    coverage_data_stats,
    expected_run_hours,
    safe_current_coverage,
    seed_staging,
)
from prepare_cams_ads_staging import prepare_ads_staging


def write_fake_om(path: Path, dimensions: tuple[int, int, int]) -> None:
    root = bytearray()
    root.extend((20, 1))
    root.extend(struct.pack("<H", 0))
    root.extend(struct.pack("<I", 0))
    root.extend(struct.pack("<Q", 128))
    root.extend(struct.pack("<Q", 256))
    root.extend(struct.pack("<Q", 3))
    root.extend(struct.pack("<f", 10.0))
    root.extend(struct.pack("<f", 0.0))
    root.extend(struct.pack("<3Q", *dimensions))
    root.extend(struct.pack("<3Q", 1, dimensions[1], dimensions[2]))
    payload = bytearray(b"OM\x03")
    payload.extend(b"\0" * (64 - len(payload)))
    payload.extend(root)
    payload.extend(b"\0" * (512 - len(payload)))
    payload.extend(b"OM\x03\0")
    payload.extend(struct.pack("<I", 0))
    payload.extend(struct.pack("<Q", 64))
    payload.extend(struct.pack("<Q", len(root)))
    path.write_bytes(payload)


def write_gfs_run(coverage: Path, run: str, index: int, run_count: int) -> None:
    from datetime import datetime, timedelta, timezone

    reference = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    hours = expected_run_hours("gfs", index, run_count)
    relative = reference.strftime("%Y/%m/%d/%H00Z")
    for domain in ("ncep_gfs013", "ncep_gfs025"):
        runtime = coverage / domain / "temperature_2m" / "chunk.om"
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.write_bytes(b"runtime")
        run_dir = coverage / "data_run" / domain / relative
        run_dir.mkdir(parents=True)
        write_fake_om(run_dir / "temperature_2m.om", (2, 3, len(hours)))
        (run_dir / "meta.json").write_text(
            json.dumps(
                {
                    "reference_time": reference.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "valid_times": [
                        (reference + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%SZ")
                        for hour in hours
                    ],
                    "variables": ["temperature_2m"],
                }
            ),
            encoding="utf-8",
        )


def write_cams_run(
    coverage: Path,
    domain: str,
    run: str,
    hours: list[int],
    variable: str,
) -> None:
    from datetime import datetime, timedelta, timezone

    reference = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    run_dir = coverage / "data_run" / domain / reference.strftime("%Y/%m/%d/%H00Z")
    run_dir.mkdir(parents=True)
    runtime = coverage / domain / variable / "chunk.om"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_bytes(b"runtime")
    write_fake_om(run_dir / f"{variable}.om", (2, 3, len(hours)))
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "reference_time": reference.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "valid_times": [
                    (reference + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    for hour in hours
                ],
                "variables": [variable],
            }
        ),
        encoding="utf-8",
    )


class SeedNativeOmStagingTests(unittest.TestCase):
    def test_marker_authority_repairs_pointer_after_publish_kill_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            old = root / "coverages" / "gfs" / "old"
            new = root / "coverages" / "gfs" / "new"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            marker = {
                "status": "complete",
                "runtime_format": "openmeteo-native-v1",
                "group": "gfs",
                "coverage_id": "old",
                "latest_complete_run": "2026071300",
                "source_runs": [
                    "2026071200",
                    "2026071206",
                    "2026071212",
                    "2026071218",
                    "2026071300",
                ],
                "coverage_path": "coverages/gfs/old",
            }
            (old / "coverage.json").write_text(json.dumps(marker), encoding="utf-8")
            marker_path = root / "groups/gfs/current/ready_for_processing.json"
            marker_path.parent.mkdir(parents=True)
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            (root / "current").mkdir()
            pointer = root / "current" / "gfs"
            pointer.symlink_to(Path("..") / "coverages" / "gfs" / "new")

            selected = safe_current_coverage(root, "gfs")

            self.assertIsNotNone(selected)
            self.assertEqual(selected[0], old.resolve())
            self.assertEqual(pointer.resolve(), old.resolve())

    def test_hardlinks_safe_older_coverage_and_reports_reused_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = root / "coverages" / "gfs" / "gfs_native_2026071300"
            existing_runs = [
                "2026071200",
                "2026071206",
                "2026071212",
                "2026071218",
                "2026071300",
            ]
            for index, run in enumerate(existing_runs):
                write_gfs_run(coverage, run, index, len(existing_runs))
            files, bytes_total = coverage_data_stats(coverage)
            marker = {
                "status": "complete",
                "runtime_format": "openmeteo-native-v1",
                "group": "gfs",
                "coverage_id": coverage.name,
                "latest_complete_run": "2026071300",
                "source_runs": existing_runs,
                "coverage_path": "coverages/gfs/gfs_native_2026071300",
                "files": files,
                "bytes": bytes_total,
            }
            (coverage / "coverage.json").write_text(json.dumps(marker), encoding="utf-8")
            marker_path = root / "groups" / "gfs" / "current" / "ready_for_processing.json"
            marker_path.parent.mkdir(parents=True)
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            (root / "current").mkdir()
            (root / "current" / "gfs").symlink_to(Path("..") / "coverages" / "gfs" / coverage.name)
            staging = root / "staging" / "gfs_test"
            desired_runs = [
                "2026071206",
                "2026071212",
                "2026071218",
                "2026071300",
                "2026071306",
            ]

            result = seed_staging(
                root,
                staging,
                "gfs",
                desired_runs,
            )

            reusable = coverage / "data_run/ncep_gfs013/2026/07/12/0600Z/temperature_2m.om"
            staged_reusable = staging / reusable.relative_to(coverage)
            shifted_full_run = coverage / "data_run/ncep_gfs013/2026/07/12/1800Z"
            self.assertEqual(
                result["reused_source_runs"],
                ["2026071206", "2026071212", "2026071300"],
            )
            self.assertEqual(result["seeded_latest_complete_run"], "2026071300")
            self.assertEqual(os.stat(reusable).st_ino, os.stat(staged_reusable).st_ino)
            self.assertFalse((staging / shifted_full_run.relative_to(coverage)).exists())
            self.assertTrue(shifted_full_run.is_dir())

    def test_does_not_seed_newer_coverage_into_older_backfill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = root / "coverages" / "cams" / "cams_native_2026071312"
            coverage.mkdir(parents=True)
            marker = {
                "status": "complete",
                "runtime_format": "openmeteo-native-v1",
                "group": "cams",
                "coverage_id": coverage.name,
                "latest_complete_run": "2026071312",
                "source_runs": ["2026071212", "2026071300", "2026071312"],
                "coverage_path": "coverages/cams/cams_native_2026071312",
                "files": 0,
                "bytes": 0,
            }
            (coverage / "coverage.json").write_text(json.dumps(marker), encoding="utf-8")
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

    def test_missing_latest_runtime_redownloads_only_latest_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = root / "coverages" / "gfs" / "gfs_native_2026071300"
            source_runs = [
                "2026071200",
                "2026071206",
                "2026071212",
                "2026071218",
                "2026071300",
            ]
            for index, run in enumerate(source_runs):
                write_gfs_run(coverage, run, index, len(source_runs))
            runtime_file = coverage / "ncep_gfs013" / "temperature_2m" / "chunk.om"
            runtime_file.parent.mkdir(parents=True, exist_ok=True)
            runtime_file.write_bytes(b"published-runtime")
            files, bytes_total = coverage_data_stats(coverage)
            marker = {
                "status": "complete",
                "runtime_format": "openmeteo-native-v1",
                "group": "gfs",
                "coverage_id": coverage.name,
                "latest_complete_run": "2026071300",
                "source_runs": source_runs,
                "coverage_path": "coverages/gfs/gfs_native_2026071300",
                "files": files,
                "bytes": bytes_total,
            }
            (coverage / "coverage.json").write_text(json.dumps(marker), encoding="utf-8")
            marker_path = root / "groups" / "gfs" / "current" / "ready_for_processing.json"
            marker_path.parent.mkdir(parents=True)
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            (root / "current").mkdir()
            (root / "current" / "gfs").symlink_to(Path("..") / "coverages" / "gfs" / coverage.name)
            current_survivor = (
                coverage
                / "data_run"
                / "ncep_gfs013"
                / "2026/07/12/0600Z"
                / "temperature_2m.om"
            )
            survivor_inode = os.stat(current_survivor).st_ino
            survivor_bytes = current_survivor.read_bytes()
            runtime_file.unlink()
            staging = root / "staging" / "gfs_rebuild"

            result = seed_staging(
                root,
                staging,
                "gfs",
                source_runs,
            )

            self.assertEqual(result["seeded_from"], str(coverage.resolve()))
            self.assertEqual(result["reused_source_runs"], source_runs[:-1])
            latest_relative = Path("2026/07/13/0000Z")
            for domain in ("ncep_gfs013", "ncep_gfs025"):
                self.assertFalse((staging / "data_run" / domain / latest_relative).exists())
            self.assertEqual(os.stat(current_survivor).st_ino, survivor_inode)
            self.assertEqual(current_survivor.read_bytes(), survivor_bytes)

    def test_cams_missing_run_redownloads_only_that_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = (
                root
                / "coverages"
                / "cams"
                / "cams_native_2026071812_main_only"
            )
            source_runs = ["2026071712", "2026071800", "2026071812"]
            for run in source_runs:
                write_cams_run(coverage, "cams_global", run, list(range(121)), "pm10")
            files, bytes_total = coverage_data_stats(coverage)
            marker = {
                "status": "complete",
                "runtime_format": "openmeteo-native-v1",
                "group": "cams",
                "coverage_id": coverage.name,
                "latest_complete_run": source_runs[-1],
                "source_runs": source_runs,
                "greenhouse_source_runs": [],
                "coverage_path": "coverages/cams/cams_native_2026071812_main_only",
                "files": files,
                "bytes": bytes_total,
            }
            (coverage / "coverage.json").write_text(json.dumps(marker), encoding="utf-8")
            marker_path = root / "groups" / "cams" / "current" / "ready_for_processing.json"
            marker_path.parent.mkdir(parents=True)
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            (root / "current").mkdir()
            (root / "current" / "cams").symlink_to(
                Path("..") / "coverages" / "cams" / coverage.name
            )

            damaged_main = coverage / "data_run/cams_global/2026/07/18/0000Z/pm10.om"
            damaged_main.unlink()
            staging = root / "staging" / "cams_repair"

            result = seed_staging(root, staging, "cams", source_runs)

            self.assertEqual(result["seeded_from"], str(coverage.resolve()))
            self.assertEqual(
                result["reused_source_runs"],
                ["2026071712", "2026071812"],
            )
            self.assertFalse(
                (staging / damaged_main.relative_to(coverage)).exists()
            )

    def test_independent_ads_seed_preserves_three_physical_greenhouse_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = (
                root
                / "coverages"
                / "cams_greenhouse"
                / "cams_greenhouse_native_2026071800_independent-v1"
            )
            source_runs = ["2026071600", "2026071700", "2026071800"]
            for run in source_runs:
                write_cams_run(
                    coverage,
                    "cams_global_greenhouse_gases",
                    run,
                    list(range(0, 121, 3)),
                    "carbon_monoxide",
                )
            runtime_file = (
                coverage
                / "cams_global_greenhouse_gases"
                / "carbon_monoxide"
                / "chunk.om"
            )
            runtime_file.parent.mkdir(parents=True, exist_ok=True)
            runtime_file.write_bytes(b"greenhouse")
            marker = {
                "status": "complete",
                "runtime_format": "openmeteo-native-v1",
                "group": "cams_greenhouse",
                "coverage_id": coverage.name,
                "latest_complete_run": source_runs[-1],
                "source_runs": source_runs,
                "coverage_path": (
                    "coverages/cams_greenhouse/"
                    "cams_greenhouse_native_2026071800_independent-v1"
                ),
                "products": {
                    "cams_global_greenhouse_gases": {
                        "runtime_domain": "cams_global_greenhouse_gases"
                    }
                },
            }
            files, bytes_total = coverage_data_stats(coverage)
            marker["files"] = files
            marker["bytes"] = bytes_total
            (coverage / "coverage.json").write_text(json.dumps(marker), encoding="utf-8")
            marker_path = (
                root
                / "groups"
                / "cams_greenhouse"
                / "current"
                / "ready_for_processing.json"
            )
            marker_path.parent.mkdir(parents=True)
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            (root / "current").mkdir()
            (root / "current" / "cams_greenhouse").symlink_to(
                Path("..")
                / "coverages"
                / "cams_greenhouse"
                / coverage.name
            )
            staging = root / "ads_staging" / "cams_ads_2026071900"

            result = prepare_ads_staging(root, staging)

            self.assertEqual(result["seeded_from"], str(coverage.resolve()))
            seeded_runtime = (
                staging
                / "cams_global_greenhouse_gases"
                / "carbon_monoxide"
                / "chunk.om"
            )
            self.assertEqual(seeded_runtime.read_bytes(), runtime_file.read_bytes())
            self.assertNotEqual(os.stat(runtime_file).st_ino, os.stat(seeded_runtime).st_ino)
            for source_run in source_runs:
                run_time = datetime.strptime(source_run, "%Y%m%d%H")
                relative = Path(
                    run_time.strftime("%Y/%m/%d/%H00Z")
                ) / "carbon_monoxide.om"
                source = (
                    coverage
                    / "data_run"
                    / "cams_global_greenhouse_gases"
                    / relative
                )
                seeded = (
                    staging
                    / "data_run"
                    / "cams_global_greenhouse_gases"
                    / relative
                )
                self.assertTrue(seeded.is_file())
                self.assertEqual(os.stat(source).st_ino, os.stat(seeded).st_ino)


if __name__ == "__main__":
    unittest.main()
