import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_native_om_coverage import (
    DEFAULT_VARIABLES,
    critical_hours,
    iso_hour,
    validate_api_payload,
    validate_coverage_contract,
)
from native_grid_contract import gfs_domain_grids
from seed_native_om_staging import coverage_data_stats


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def make_coverage(root: Path) -> Path:
    coverage = root / "coverages" / "gfs" / "gfs_native_2026071300"
    manifest = {
        "status": "complete",
        "runtime_format": "openmeteo-native-v1",
        "group": "gfs",
        "coverage_id": "gfs_native_2026071300",
        "latest_complete_run": "2026071300",
        "source_runs": ["2026071200", "2026071206", "2026071212", "2026071218", "2026071300"],
        "public_start_utc": "2026-07-12T00:00:00Z",
        "local_day_start_utc": "2026-07-12T16:00:00Z",
        "public_end_utc": "2026-07-29T00:00:00Z",
        "public_hours": 408,
        "historical_max_forecast_hour": 5,
        "latest_max_forecast_hour": 384,
        "short_run_count": 3,
        "full_run_count": 2,
        "source_run_max_forecast_hours": [5, 5, 5, 384, 384],
        "domain_grids": gfs_domain_grids(),
        "static_sources": {
            "copernicus_dem90": {
                "source": "copernicus_dem90",
                "runtime_path": "copernicus_dem90/static",
                "latitude_chunk_min": 0,
                "latitude_chunk_max": 0,
                "file_count": 1,
            }
        },
    }
    write_json(coverage / "coverage.json", manifest)
    dem = coverage / "copernicus_dem90" / "static" / "lat_0.om"
    dem.parent.mkdir(parents=True, exist_ok=True)
    dem.write_bytes(b"dem")
    for domain in ("ncep_gfs013", "ncep_gfs025"):
        runtime = coverage / domain / "temperature_2m" / "chunk.om"
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b"runtime")
        write_json(
            coverage / "data_run" / domain / "latest.json",
            {"reference_time": "2026-07-13T00:00:00Z", "valid_times": ["2026-07-13T00:00Z"]},
        )
        for source_index, source_run in enumerate(manifest["source_runs"]):
            base = datetime.strptime(source_run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
            forecast_hours = (
                list(range(6))
                if source_index < manifest["short_run_count"]
                else list(range(385))
            )
            run_dir = coverage / "data_run" / domain / base.strftime("%Y/%m/%d/%H00Z")
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "temperature_2m.om").write_bytes(b"run")
            write_json(
                run_dir / "meta.json",
                {
                    "reference_time": base.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "valid_times": [
                        (base + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%MZ")
                        for hour in forecast_hours
                    ],
                    "variables": ["temperature_2m"],
                },
            )
    files, bytes_total = coverage_data_stats(coverage)
    manifest["files"] = files
    manifest["bytes"] = bytes_total
    write_json(coverage / "coverage.json", manifest)
    marker = dict(manifest)
    marker["coverage_path"] = "coverages/gfs/gfs_native_2026071300"
    write_json(root / "groups" / "gfs" / "current" / "ready_for_processing.json", marker)
    current = root / "current"
    current.mkdir(parents=True)
    (current / "gfs").symlink_to(Path("..") / "coverages" / "gfs" / coverage.name)
    return coverage


class ValidateNativeOmCoverageTests(unittest.TestCase):
    def test_rejects_missing_aggregate_runtime_file_even_when_runs_are_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = make_coverage(root)
            (coverage / "ncep_gfs013" / "temperature_2m" / "chunk.om").unlink()

            with self.assertRaisesRegex(ValueError, "missing runtime variables"):
                validate_coverage_contract(root)

    def test_accepts_consistent_five_run_native_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = make_coverage(root)

            contract = validate_coverage_contract(root)

            self.assertEqual(contract["coverage_path"], str(coverage.resolve()))
            self.assertEqual(contract["public_hours"], 408)
            self.assertEqual(len(critical_hours(contract)), 8)

    def test_rejects_current_pointer_that_does_not_match_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            make_coverage(root)
            wrong = root / "coverages" / "gfs" / "wrong"
            wrong.mkdir()
            (root / "current" / "gfs").unlink()
            (root / "current" / "gfs").symlink_to(wrong)

            with self.assertRaisesRegex(ValueError, "does not select"):
                validate_coverage_contract(root)

    def test_rejects_overlapping_f006_historical_horizon_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            coverage = make_coverage(root)
            manifest_path = coverage / "coverage.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["historical_max_forecast_hour"] = 6
            write_json(manifest_path, manifest)
            marker_path = (
                root / "groups" / "gfs" / "current" / "ready_for_processing.json"
            )
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker["historical_max_forecast_hour"] = 6
            write_json(marker_path, marker)

            with self.assertRaisesRegex(ValueError, "historical GFS horizon must be 5h"):
                validate_coverage_contract(root)

    def test_requires_finite_values_at_every_critical_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            make_coverage(root)
            contract = validate_coverage_contract(root)
            hours = critical_hours(contract)
            time_values = [iso_hour(hour) for _, hour in hours]
            hourly = {"time": time_values}
            for variable in DEFAULT_VARIABLES:
                hourly[variable] = [float(index) for index in range(len(time_values))]

            result = validate_api_payload(
                [{"hourly": hourly}],
                points=[(31.2304, 121.4737)],
                variables=list(DEFAULT_VARIABLES),
                hours=hours,
            )

            self.assertTrue(result["passed"])
            self.assertFalse(result["failures"])

    def test_rejects_all_null_variable_at_384_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            make_coverage(root)
            contract = validate_coverage_contract(root)
            hours = critical_hours(contract)
            time_values = [iso_hour(hour) for _, hour in hours]
            hourly = {"time": time_values}
            for variable in DEFAULT_VARIABLES:
                hourly[variable] = [1.0] * len(time_values)
            hourly["temperature_850hPa"][-1] = None

            result = validate_api_payload(
                [{"hourly": hourly}],
                points=[(31.2304, 121.4737)],
                variables=list(DEFAULT_VARIABLES),
                hours=hours,
            )

            self.assertFalse(result["passed"])
            self.assertIn(
                "all_null_or_non_finite",
                [failure["reason"] for failure in result["failures"]],
            )

    def test_records_expected_skip_hour_zero_missing_at_source_run_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "producer"
            make_coverage(root)
            contract = validate_coverage_contract(root)
            source_run_reference = datetime(2026, 7, 12, 6, tzinfo=timezone.utc)
            hours = [("retained_source_run", source_run_reference)]
            time_values = [iso_hour(hour) for _, hour in hours]
            hourly = {"time": time_values}
            variables = [*DEFAULT_VARIABLES, "latent_heat_flux"]
            for variable in variables:
                hourly[variable] = [1.0] * len(time_values)
            hourly["uv_index_clear_sky"][0] = None
            hourly["latent_heat_flux"][0] = None

            result = validate_api_payload(
                [{"hourly": hourly}],
                points=[(31.2304, 121.4737)],
                variables=variables,
                hours=hours,
                source_run_references={
                    datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
                    for run in contract["source_runs"]
                },
            )
            self.assertTrue(result["passed"])
            evidence = result["critical_hours"]["retained_source_run"]["variables"][
                "uv_index_clear_sky"
            ]
            self.assertTrue(evidence["expected_missing"])
            self.assertEqual(
                evidence["expected_missing_reason"],
                "official_gfs_skip_hour_zero_at_source_run_reference",
            )
            self.assertTrue(
                result["critical_hours"]["retained_source_run"]["variables"][
                    "latent_heat_flux"
                ]["expected_missing"]
            )

    def test_rejects_skip_hour_zero_missing_away_from_source_run_boundary(self):
        non_boundary = datetime(2026, 7, 13, 1, tzinfo=timezone.utc)
        hourly = {
            "time": [iso_hour(non_boundary)],
            "uv_index_clear_sky": [None],
        }

        result = validate_api_payload(
            [{"hourly": hourly}],
            points=[(31.2304, 121.4737)],
            variables=["uv_index_clear_sky"],
            hours=[("non_boundary", non_boundary)],
            source_run_references={
                datetime(2026, 7, 13, 0, tzinfo=timezone.utc),
            },
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"][0]["reason"], "all_null_or_non_finite")
        self.assertFalse(
            result["critical_hours"]["non_boundary"]["variables"][
                "uv_index_clear_sky"
            ]["expected_missing"]
        )

    def test_rejects_regular_variable_missing_at_source_run_boundary(self):
        source_run_reference = datetime(2026, 7, 13, 0, tzinfo=timezone.utc)
        hourly = {
            "time": [iso_hour(source_run_reference)],
            "temperature_2m": [None],
        }

        result = validate_api_payload(
            [{"hourly": hourly}],
            points=[(31.2304, 121.4737)],
            variables=["temperature_2m"],
            hours=[("latest_run", source_run_reference)],
            source_run_references={source_run_reference},
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"][0]["reason"], "all_null_or_non_finite")
        self.assertFalse(
            result["critical_hours"]["latest_run"]["variables"]["temperature_2m"][
                "expected_missing"
            ]
        )


if __name__ == "__main__":
    unittest.main()
