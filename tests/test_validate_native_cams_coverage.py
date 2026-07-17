from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from native_grid_contract import cams_domain_grids
from validate_native_cams_coverage import validate_cams_contract


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


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def make_cams_coverage(root: Path) -> Path:
    coverage = root / "coverages" / "cams" / "cams_native_2026071312"
    source_runs = ["2026071212", "2026071300", "2026071312"]
    greenhouse_source_runs = ["2026070900", "2026071000", "2026071100"]
    manifest = {
        "status": "complete",
        "runtime_format": "openmeteo-native-v1",
        "group": "cams",
        "coverage_id": coverage.name,
        "latest_complete_run": source_runs[-1],
        "source_runs": source_runs,
        "greenhouse_source_runs": greenhouse_source_runs,
        "public_start_utc": "2026-07-12T12:00:00Z",
        "local_day_start_utc": "2026-07-12T16:00:00Z",
        "public_end_utc": "2026-07-18T12:00:00Z",
        "public_hours": 144,
        "domain_grids": cams_domain_grids(),
    }
    write_json(coverage / "coverage.json", manifest)
    (coverage / "cams_global").mkdir(parents=True)
    (coverage / "cams_global_greenhouse_gases").mkdir(parents=True)
    for source_run in source_runs:
        base = datetime.strptime(source_run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        run_dir = coverage / "data_run" / "cams_global" / base.strftime("%Y/%m/%d/%H00Z")
        run_dir.mkdir(parents=True)
        (run_dir / "pm2_5.om").write_bytes(b"cams")
        write_json(
            run_dir / "meta.json",
            {
                "reference_time": base.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "valid_times": [
                    (base + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%MZ")
                    for hour in range(121)
                ],
                "variables": ["pm2_5"],
            },
        )
    for source_run in greenhouse_source_runs:
        base = datetime.strptime(source_run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        run_dir = (
            coverage
            / "data_run"
            / "cams_global_greenhouse_gases"
            / base.strftime("%Y/%m/%d/%H00Z")
        )
        run_dir.mkdir(parents=True)
        write_fake_om(run_dir / "carbon_monoxide.om", (2, 3, 41))
        write_json(
            run_dir / "meta.json",
            {
                "reference_time": base.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "valid_times": [
                    (base + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%MZ")
                    for hour in range(0, 121, 3)
                ],
                "variables": ["carbon_monoxide"],
            },
        )
    marker = dict(manifest)
    marker["coverage_path"] = f"coverages/cams/{coverage.name}"
    write_json(root / "groups" / "cams" / "current" / "ready_for_processing.json", marker)
    (root / "current").mkdir(parents=True)
    (root / "current" / "cams").symlink_to(Path("..") / "coverages" / "cams" / coverage.name)
    return coverage


class ValidateNativeCamsCoverageTests(unittest.TestCase):
    def test_accepts_three_complete_cams_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = make_cams_coverage(root)

            contract = validate_cams_contract(root)

            self.assertEqual(contract["coverage_path"], str(coverage.resolve()))
            self.assertEqual(contract["source_runs"], ["2026071212", "2026071300", "2026071312"])
            self.assertEqual(
                contract["greenhouse_source_runs"],
                ["2026070900", "2026071000", "2026071100"],
            )

    def test_rejects_missing_historical_cams_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = make_cams_coverage(root)
            missing = coverage / "data_run" / "cams_global" / "2026/07/12/1200Z"
            for path in sorted(missing.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
            missing.rmdir()

            with self.assertRaisesRegex(ValueError, "missing retained run metadata"):
                validate_cams_contract(root)


if __name__ == "__main__":
    unittest.main()
