import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_source_run_plan import plan_source_runs


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


def load_publisher_module():
    path = ROOT / "scripts" / "publish_native_om_coverage.py"
    spec = importlib.util.spec_from_file_location("publish_native_om_coverage", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_staging(output_root: Path, run: str) -> Path:
    staging = output_root / "staging" / f"gfs_{run}_test"
    dem = staging / "copernicus_dem90" / "static" / "lat_0.om"
    dem.parent.mkdir(parents=True, exist_ok=True)
    dem.write_bytes(b"dem")
    reference = f"{run[0:4]}-{run[4:6]}-{run[6:8]}T{run[8:10]}:00:00Z"
    plan = plan_source_runs(
        run,
        cadence_hours=6,
        source_run_count=5,
        historical_max_forecast_hour=5,
        latest_max_forecast_hour=384,
        local_utc_offset_hours=8,
        full_run_count=2,
    )
    for domain in ("ncep_gfs013", "ncep_gfs025"):
        (staging / domain / "temperature_2m").mkdir(parents=True)
        (staging / domain / "temperature_2m" / "chunk.om").write_bytes(b"om")
        latest = staging / "data_run" / domain / "latest.json"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(
            json.dumps(
                {
                    "reference_time": reference,
                    "valid_times": [reference.replace(":00:00Z", ":00Z")],
                }
            ),
            encoding="utf-8",
        )
        for source_index, source_run in enumerate(plan.source_runs):
            base = datetime.strptime(source_run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
            forecast_hours = (
                list(range(6))
                if source_index < 3
                else list(range(121)) + list(range(123, 385, 3))
            )
            stored_frames = 6 if source_index < 3 else 209
            run_dir = staging / "data_run" / domain / base.strftime("%Y/%m/%d/%H00Z")
            run_dir.mkdir(parents=True, exist_ok=True)
            write_fake_om(run_dir / "temperature_2m.om", (2, 3, stored_frames))
            (run_dir / "meta.json").write_text(
                json.dumps(
                    {
                        "reference_time": base.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "valid_times": [
                            (base + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%MZ")
                            for hour in forecast_hours
                        ],
                        "variables": ["temperature_2m"],
                    }
                ),
                encoding="utf-8",
            )
    return staging


def publisher_args(output_root: Path, staging: Path, run: str) -> argparse.Namespace:
    plan = plan_source_runs(
        run,
        cadence_hours=6,
        source_run_count=5,
        historical_max_forecast_hour=5,
        latest_max_forecast_hour=384,
        local_utc_offset_hours=8,
        full_run_count=2,
    )
    return argparse.Namespace(
        group="gfs",
        staging_dir=str(staging),
        output_root=str(output_root),
        latest_run=run,
        source_runs=",".join(plan.source_runs),
        full_run_count=2,
        historical_max_forecast_hour=5,
        latest_max_forecast_hour=384,
        public_start_utc=plan.public_start_utc,
        public_end_utc=plan.public_end_utc,
        public_hours=plan.public_hours,
        local_day_start_utc=plan.local_day_start_utc,
        min_public_hours=300,
        keep_coverages=3,
        required_gfs013_variables="temperature_2m",
        required_gfs025_variables="temperature_2m",
        required_pressure_levels="",
        required_pressure_variables="",
        required_dem_lat_min=0,
        required_dem_lat_max=0,
    )


def make_cams_staging(output_root: Path, run: str) -> Path:
    staging = output_root / "staging" / f"cams_{run}_test"
    reference = f"{run[0:4]}-{run[4:6]}-{run[6:8]}T{run[8:10]}:00:00Z"
    domain = staging / "cams_global" / "pm2_5"
    domain.mkdir(parents=True)
    (domain / "chunk.om").write_bytes(b"cams")
    latest = staging / "data_run" / "cams_global" / "latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text(
        json.dumps(
            {
                "reference_time": reference,
                "valid_times": [
                    reference.replace(":00:00Z", ":00Z"),
                    "2026-07-18T00:00Z",
                ],
            }
        ),
        encoding="utf-8",
    )
    base_latest = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    for offset in (24, 12, 0):
        base = base_latest - timedelta(hours=offset)
        run_dir = staging / "data_run" / "cams_global" / base.strftime("%Y/%m/%d/%H00Z")
        run_dir.mkdir(parents=True, exist_ok=True)
        write_fake_om(run_dir / "pm2_5.om", (2, 3, 121))
        write_fake_om(run_dir / "dust.om", (2, 3, 121))
        (run_dir / "meta.json").write_text(
            json.dumps(
                {
                    "reference_time": base.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "valid_times": [
                        (base + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%MZ")
                        for hour in range(121)
                    ],
                    "variables": ["pm2_5", "dust"],
                }
            ),
            encoding="utf-8",
        )
    greenhouse_domain = (
        staging / "cams_global_greenhouse_gases" / "carbon_monoxide"
    )
    greenhouse_domain.mkdir(parents=True)
    (greenhouse_domain / "chunk.om").write_bytes(b"greenhouse")
    greenhouse_latest_base = base_latest.replace(hour=0) - timedelta(days=2)
    greenhouse_latest = (
        staging / "data_run" / "cams_global_greenhouse_gases" / "latest.json"
    )
    greenhouse_latest.parent.mkdir(parents=True)
    greenhouse_latest.write_text(
        json.dumps(
            {
                "reference_time": greenhouse_latest_base.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "valid_times": [
                    (greenhouse_latest_base + timedelta(hours=hour)).strftime(
                        "%Y-%m-%dT%H:%MZ"
                    )
                    for hour in range(0, 121, 3)
                ],
            }
        ),
        encoding="utf-8",
    )
    for offset in (2, 1, 0):
        base = greenhouse_latest_base - timedelta(days=offset)
        run_dir = (
            staging
            / "data_run"
            / "cams_global_greenhouse_gases"
            / base.strftime("%Y/%m/%d/%H00Z")
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        write_fake_om(run_dir / "carbon_monoxide.om", (2, 3, 41))
        (run_dir / "meta.json").write_text(
            json.dumps(
                {
                    "reference_time": base.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "valid_times": [
                        (base + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%MZ")
                        for hour in range(0, 121, 3)
                    ],
                    "variables": ["carbon_monoxide"],
                }
            ),
            encoding="utf-8",
        )
    return staging


class NativeOmProducerTests(unittest.TestCase):
    def test_publishes_immutable_coverage_and_ready_marker(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            staging = make_staging(output_root, "2026071300")

            ready = publisher.publish_gfs_coverage(
                publisher_args(output_root, staging, "2026071300")
            )

            coverage = output_root / "coverages" / "gfs" / "gfs_native_2026071300"
            marker = output_root / "groups" / "gfs" / "current" / "ready_for_processing.json"
            self.assertFalse(staging.exists())
            self.assertTrue((coverage / "coverage.json").is_file())
            self.assertTrue(marker.is_file())
            self.assertEqual(ready["runtime_format"], "openmeteo-native-v1")
            self.assertEqual(
                ready["source_runs"],
                ["2026071200", "2026071206", "2026071212", "2026071218", "2026071300"],
            )
            self.assertEqual(ready["public_hours"], 408)
            self.assertEqual(ready["short_run_count"], 3)
            self.assertEqual(ready["full_run_count"], 2)
            self.assertEqual(
                ready["source_run_max_forecast_hours"], [5, 5, 5, 384, 384]
            )
            self.assertEqual(
                ready["coverage_path"], "coverages/gfs/gfs_native_2026071300"
            )
            self.assertEqual(
                ready["products"]["gfs_pressure_profile"]["runtime_domain"],
                "ncep_gfs025",
            )
            self.assertEqual(ready["domain_grids"]["ncep_gfs013"]["nx"], 597)
            self.assertEqual(ready["domain_grids"]["ncep_gfs013"]["ny"], 495)
            self.assertEqual(
                ready["products"]["gfs013_surface"]["grid"],
                ready["domain_grids"]["ncep_gfs013"],
            )
            current = output_root / "current" / "gfs"
            self.assertTrue(current.is_symlink())
            self.assertEqual(current.resolve(), coverage.resolve())

    def test_same_run_repair_uses_distinct_revisioned_coverage(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            first = make_staging(output_root, "2026071300")
            publisher.publish_gfs_coverage(publisher_args(output_root, first, "2026071300"))

            repaired = make_staging(output_root, "2026071300")
            args = publisher_args(output_root, repaired, "2026071300")
            args.coverage_revision = "uv-clear-v1"
            args.keep_coverages = 1
            ready = publisher.publish_gfs_coverage(args)

            self.assertEqual(ready["coverage_id"], "gfs_native_2026071300_uv-clear-v1")
            self.assertEqual(
                (output_root / "current" / "gfs").resolve().name,
                "gfs_native_2026071300_uv-clear-v1",
            )
            self.assertTrue(
                (output_root / "coverages" / "gfs" / "gfs_native_2026071300").exists()
            )

    def test_same_run_revision_retention_keeps_current_and_immediately_previous(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            first = make_staging(output_root, "2026071300")
            first_args = publisher_args(output_root, first, "2026071300")
            first_args.keep_coverages = 3
            publisher.publish_gfs_coverage(first_args)

            previous = make_staging(output_root, "2026071300")
            previous_args = publisher_args(output_root, previous, "2026071300")
            previous_args.coverage_revision = "repair-v1"
            previous_args.keep_coverages = 3
            publisher.publish_gfs_coverage(previous_args)

            coverages_root = output_root / "coverages" / "gfs"
            generated_at_by_coverage = {
                "gfs_native_2026071300": "2000-01-01T00:00:00Z",
                "gfs_native_2026071300_repair-v1": "2001-01-01T00:00:00Z",
            }
            for coverage_id, generated_at in generated_at_by_coverage.items():
                manifest_path = coverages_root / coverage_id / "coverage.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["generated_at"] = generated_at
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            current = make_staging(output_root, "2026071300")
            current_args = publisher_args(output_root, current, "2026071300")
            current_args.coverage_revision = "repair-v2"
            current_args.keep_coverages = 1
            publisher.publish_gfs_coverage(current_args)

            self.assertEqual(
                sorted(path.name for path in coverages_root.iterdir()),
                [
                    "gfs_native_2026071300_repair-v1",
                    "gfs_native_2026071300_repair-v2",
                ],
            )
            self.assertEqual(
                (output_root / "current" / "gfs").resolve().name,
                "gfs_native_2026071300_repair-v2",
            )

    def test_revisioned_coverage_cannot_reuse_legacy_horizon_identity(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            first = make_staging(output_root, "2026071300")
            first_args = publisher_args(output_root, first, "2026071300")
            first_args.coverage_revision = "three-short-two-full-v1"
            publisher.publish_gfs_coverage(first_args)

            coverage = (
                output_root
                / "coverages"
                / "gfs"
                / "gfs_native_2026071300_three-short-two-full-v1"
            )
            manifest_path = coverage / "coverage.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["historical_max_forecast_hour"] = 6
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            retry = make_staging(output_root, "2026071300")
            retry_args = publisher_args(output_root, retry, "2026071300")
            retry_args.coverage_revision = "three-short-two-full-v1"
            with self.assertRaisesRegex(
                ValueError, "identity mismatch for historical_max_forecast_hour"
            ):
                publisher.publish_gfs_coverage(retry_args)

            self.assertTrue(retry.exists())

    def test_rejects_unsafe_coverage_revision(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            staging = make_staging(output_root, "2026071300")
            args = publisher_args(output_root, staging, "2026071300")
            args.coverage_revision = "../unsafe"
            with self.assertRaisesRegex(ValueError, "coverage_revision"):
                publisher.publish_gfs_coverage(args)

    def test_retains_only_configured_complete_coverages(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            for run in ("2026071212", "2026071218", "2026071300", "2026071306"):
                staging = make_staging(output_root, run)
                args = publisher_args(output_root, staging, run)
                args.keep_coverages = 3
                publisher.publish_gfs_coverage(args)

            coverages = sorted(
                path.name for path in (output_root / "coverages" / "gfs").iterdir()
            )
            self.assertEqual(
                coverages,
                [
                    "gfs_native_2026071218",
                    "gfs_native_2026071300",
                    "gfs_native_2026071306",
                ],
            )

    def test_rejects_latest_gfs_run_shorter_than_complete_horizon(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            staging = make_staging(output_root, "2026071300")
            args = publisher_args(output_root, staging, "2026071300")
            short_plan = plan_source_runs(
                "2026071300",
                cadence_hours=6,
                source_run_count=5,
                historical_max_forecast_hour=5,
                latest_max_forecast_hour=291,
                local_utc_offset_hours=8,
                full_run_count=2,
            )
            args.latest_max_forecast_hour = 291
            args.public_end_utc = short_plan.public_end_utc
            args.public_hours = short_plan.public_hours

            with self.assertRaisesRegex(ValueError, "complete 0...384h"):
                publisher.publish_gfs_coverage(args)

            self.assertTrue(staging.exists())

    def test_rejects_overlapping_f006_historical_horizon_contract(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            staging = make_staging(output_root, "2026071300")
            args = publisher_args(output_root, staging, "2026071300")
            args.historical_max_forecast_hour = 6

            with self.assertRaisesRegex(ValueError, "forecast hours 0 through 5"):
                publisher.publish_gfs_coverage(args)

            self.assertTrue(staging.exists())

    def test_rejects_historical_gfs_run_that_still_contains_full_horizon(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            staging = make_staging(output_root, "2026071300")
            previous = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
            meta_path = (
                staging
                / "data_run"
                / "ncep_gfs013"
                / previous.strftime("%Y/%m/%d/%H00Z")
                / "meta.json"
            )
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            payload["valid_times"].append(
                (previous + timedelta(hours=7)).strftime("%Y-%m-%dT%H:%MZ")
            )
            meta_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "valid_times do not match"):
                publisher.publish_gfs_coverage(
                    publisher_args(output_root, staging, "2026071300")
                )

    def test_seed_validation_rejects_history_with_f006_overlap(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            staging = make_staging(output_root, "2026071300")
            previous = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
            run_dir = (
                staging
                / "data_run"
                / "ncep_gfs013"
                / previous.strftime("%Y/%m/%d/%H00Z")
            )
            meta_path = run_dir / "meta.json"
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            payload["valid_times"].append(
                (previous + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%MZ")
            )
            meta_path.write_text(json.dumps(payload), encoding="utf-8")
            write_fake_om(run_dir / "temperature_2m.om", (2, 3, 6))

            with self.assertRaisesRegex(ValueError, "forecast hours 0...5"):
                publisher.validate_gfs_retained_run(
                    staging,
                    "2026071212",
                    5,
                    {
                        "ncep_gfs013": {"temperature_2m"},
                        "ncep_gfs025": {"temperature_2m"},
                    },
                )

    def test_rejects_latest_gfs_om_with_wrong_stored_source_frame_count(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            staging = make_staging(output_root, "2026071300")
            latest = datetime(2026, 7, 13, 0, tzinfo=timezone.utc)
            om_path = (
                staging
                / "data_run"
                / "ncep_gfs013"
                / latest.strftime("%Y/%m/%d/%H00Z")
                / "temperature_2m.om"
            )
            write_fake_om(om_path, (2, 3, 208))

            with self.assertRaisesRegex(ValueError, "stored time count 208, expected 209"):
                publisher.publish_gfs_coverage(
                    publisher_args(output_root, staging, "2026071300")
                )

    def test_gfs_stored_frame_contract_accounts_for_missing_hour_zero(self):
        publisher = load_publisher_module()

        gfs013 = publisher.gfs_stored_frame_counts(
            "ncep_gfs013",
            ["temperature_2m", "precipitation", "cloud_cover", "uv_index"],
            209,
        )
        gfs025 = publisher.gfs_stored_frame_counts(
            "ncep_gfs025",
            ["pressure_msl", "categorical_freezing_rain", "temperature_1000hPa"],
            209,
        )

        self.assertEqual(gfs013["temperature_2m"], 209)
        self.assertEqual(gfs013["precipitation"], 208)
        self.assertEqual(gfs013["cloud_cover"], 208)
        self.assertEqual(gfs013["uv_index"], 208)
        self.assertEqual(gfs025["pressure_msl"], 209)
        self.assertEqual(gfs025["categorical_freezing_rain"], 208)
        self.assertEqual(gfs025["temperature_1000hPa"], 209)

        historical = publisher.gfs_stored_frame_counts(
            "ncep_gfs013",
            ["temperature_2m", "precipitation", "cloud_cover"],
            6,
        )
        self.assertEqual(historical["temperature_2m"], 6)
        self.assertEqual(historical["precipitation"], 5)
        self.assertEqual(historical["cloud_cover"], 5)

    def test_rejects_gfs_coverage_missing_required_pressure_level_file(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            staging = make_staging(output_root, "2026071300")
            args = publisher_args(output_root, staging, "2026071300")
            args.required_pressure_levels = "1000"
            args.required_pressure_variables = "temperature"

            with self.assertRaisesRegex(
                ValueError, "missing required variables: temperature_1000hPa"
            ):
                publisher.publish_gfs_coverage(args)

    def test_pressure_msl_belongs_only_to_gfs025_contract(self):
        publisher = load_publisher_module()

        self.assertNotIn("pressure_msl", publisher.DEFAULT_GFS013_REQUIRED.split(","))
        self.assertIn("pressure_msl", publisher.DEFAULT_GFS025_REQUIRED.split(","))

    def test_resumes_marker_publish_after_coverage_was_already_moved(self):
        publisher = load_publisher_module()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            first_staging = make_staging(output_root, "2026071300")
            publisher.publish_gfs_coverage(
                publisher_args(output_root, first_staging, "2026071300")
            )

            marker = output_root / "groups" / "gfs" / "current" / "ready_for_processing.json"
            marker.unlink()
            retry_staging = make_staging(output_root, "2026071300")

            ready = publisher.publish_gfs_coverage(
                publisher_args(output_root, retry_staging, "2026071300")
            )

            self.assertTrue(marker.is_file())
            self.assertFalse(retry_staging.exists())
            self.assertTrue(ready["coverage_reused"])

    def test_gfs_om_stage_is_producer_only_inside_event_pipeline(self):
        producer = (ROOT / "scripts" / "run_gfs_om_production_cycle.sh").read_text(
            encoding="utf-8"
        )
        downloader = (ROOT / "scripts" / "download_openmeteo_gfs_data.sh").read_text(
            encoding="utf-8"
        )
        scheduler = (ROOT / "scripts" / "run_gfs_probe_and_cycle.sh").read_text(
            encoding="utf-8"
        )
        pipeline = (ROOT / "scripts" / "run_native_model_pipeline.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('WEATHER_GFS_RUN="$SOURCE_RUN"', producer)
        self.assertIn('WEATHER_GFS_RUN="$RUN"', producer)
        self.assertLess(
            producer.index('WEATHER_GFS_RUN="$SOURCE_RUN"'),
            producer.index('WEATHER_GFS_RUN="$RUN"'),
        )
        self.assertIn('HISTORY_MAX_FORECAST_HOUR="${WEATHER_GFS_REQUIRED_HISTORY_FORECAST_HOUR:-5}"', producer)
        self.assertIn('FULL_RUN_COUNT="${WEATHER_GFS_REQUIRED_FULL_RUN_COUNT:-2}"', producer)
        self.assertIn("validate_staged_gfs_run", producer)
        self.assertIn('validate_staged_gfs_run "$SOURCE_RUN" "$SOURCE_MAX_FORECAST_HOUR"', producer)
        self.assertIn('validate_staged_gfs_run "$RUN" "$LATEST_MAX_FORECAST_HOUR"', producer)
        self.assertIn('restore_latest_metadata "$RUN"', producer)
        self.assertIn("reuse validated latest run=$RUN", producer)
        self.assertIn('KEEP_COVERAGES="${WEATHER_OM_GFS_KEEP_COVERAGES:-1}"', producer)
        self.assertIn('RESUME_STAGING="${WEATHER_OM_GFS_RESUME_STAGING:-}"', producer)
        self.assertIn('cp -al -- "$RESUME_SOURCE" "$STAGING_DIR"', producer)
        self.assertIn('"$(dirname -- "$RESUME_SOURCE")" != "$RESUME_ROOT"', producer)
        self.assertIn("seed_native_om_staging.py", producer)
        self.assertIn('SEEDED_LATEST_RUN="$(python3 -c', producer)
        self.assertNotIn('"$SOURCE_RUN" != "$SEEDED_LATEST_RUN"', producer)
        self.assertIn('--full-run-count "$FULL_RUN_COUNT"', producer)
        self.assertIn("prune_native_om_runs.py", producer)
        self.assertIn("publish_native_om_coverage.py", producer)
        self.assertNotIn("build_openmeteo_gfs_layers.sh", producer)
        self.assertNotIn("build_webp.py", producer)
        self.assertNotIn("run_openmeteo_api_server.sh", producer)
        self.assertIn("run_native_model_pipeline.sh gfs", scheduler)
        self.assertIn("run_gfs_om_production_cycle.sh", pipeline)
        self.assertNotIn("run_gfs_production_cycle.sh", scheduler)
        self.assertIn('GFS_SKIP_GFS013="${WEATHER_GFS_SKIP_GFS013:-false}"', downloader)
        self.assertIn('GFS_SKIP_GFS025_SURFACE="${WEATHER_GFS_SKIP_GFS025_SURFACE:-false}"', downloader)
        self.assertIn('GFS_PRESERVE_HTTP_CACHE="${WEATHER_GFS_PRESERVE_HTTP_CACHE:-false}"', downloader)
        self.assertIn('if is_truthy "$GFS_SKIP_GFS013"', downloader)
        self.assertIn('if is_truthy "$GFS_SKIP_GFS025_SURFACE"', downloader)
        self.assertIn('REPAIR_PRESSURE_ONLY="${WEATHER_OM_GFS_REPAIR_PRESSURE_ONLY:-false}"', producer)
        self.assertIn('export WEATHER_GFS_SKIP_GFS025_SURFACE=true', producer)
        self.assertIn('for domain in ncep_gfs013 ncep_gfs025; do', producer)

    def test_vendored_swift_importer_preserves_existing_om_time_offsets(self):
        splitter = (
            ROOT
            / "vendor"
            / "open-meteo"
            / "Sources"
            / "App"
            / "Helper"
            / "OmFileSplitter.swift"
        ).read_text(encoding="utf-8")
        update = splitter.split(
            "func updateFromTimeOrientedStreaming3D", 1
        )[1].split("func updateRollingTimeSeries", 1)[0]

        self.assertIn("Read existing data for a range of locations", update)
        self.assertIn("if data[data.startIndex + l * nIndexTime + tArray].isNaN", update)
        self.assertIn("fileData[nTimePerFile * l + tFile] = data", update)
        self.assertIn("try writer.writeFn.linkTemporary(file: writer.fileName)", update)

    def test_vendored_full_run_writer_replaces_previous_full_horizon_file(self):
        writer_source = (
            ROOT
            / "vendor"
            / "open-meteo"
            / "Sources"
            / "App"
            / "Helper"
            / "Writer"
            / "GenericVariableHandle.swift"
        ).read_text(encoding="utf-8")
        full_run = writer_source.split("static func generateFullRunData", 1)[1].split(
            "private static func convertSerial3D", 1
        )[0]

        self.assertIn("let nTime = time.count", full_run)
        self.assertIn(
            "FileHandle.createNewFile(file: filePath, overwrite: true, temporary: true)",
            full_run,
        )
        self.assertIn(
            "dimensions = nMembers > 1 ? [ny, nx, nMembers, nTime] : [ny, nx, nTime]",
            full_run,
        )

    def test_full_run_pressure_levels_match_shanghai_profile_contract(self):
        source = (
            ROOT
            / "vendor"
            / "open-meteo"
            / "Sources"
            / "App"
            / "Helper"
            / "FullRunsVariables.swift"
        ).read_text(encoding="utf-8")
        expected = "[1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150, 100, 50]"

        self.assertIn(expected, source)
        self.assertIn(expected[1:-1], (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8").replace(",", ", "))

    def test_swift_candidate_image_uses_source_identity_without_overwriting_latest(self):
        source = (ROOT / "scripts" / "build_openmeteo_image.sh").read_text(encoding="utf-8")

        self.assertIn('IMAGE_TAG="native-$SOURCE_ID"', source)
        self.assertIn("git -C \"$REPO_ROOT\" diff --binary", source)
        self.assertIn('WEATHER_OPENMETEO_TAG_LATEST:-false', source)
        self.assertNotIn('--tag "$IMAGE_NAME:latest" \\\n', source)

    def test_swift_imports_leave_cpu_and_io_headroom_for_client_api(self):
        runtime = (ROOT / "scripts" / "openmeteo_runtime_common.sh").read_text(encoding="utf-8")
        config = (ROOT / "config" / "singapore.example.env").read_text(encoding="utf-8")

        self.assertIn('--cpus "$OPENMETEO_CPU_LIMIT"', runtime)
        self.assertIn('--cpu-shares "$OPENMETEO_CPU_SHARES"', runtime)
        self.assertIn('--blkio-weight "$OPENMETEO_BLKIO_WEIGHT"', runtime)
        self.assertIn("WEATHER_OPENMETEO_CPU_LIMIT=1.5", config)
        self.assertIn("WEATHER_OPENMETEO_CPU_SHARES=256", config)
        self.assertIn("WEATHER_OPENMETEO_BLKIO_WEIGHT=100", config)
        self.assertIn("OM_WEBP_WORKERS=1", config)
        self.assertIn("WEATHER_OPENMETEO_TAG=native-REPLACE_WITH_PRINTED_SOURCE_ID", config)
        self.assertIn("WEATHER_OPENMETEO_TAG must be the exact immutable", runtime)
        self.assertIn('find "$staging_dir" -type d -exec chown', runtime)
        self.assertNotIn('IMAGE_TAG="${WEATHER_OPENMETEO_TAG:-$(default_image_tag)}"', runtime)
        self.assertNotIn("MIN_FREE", config)

    def test_swift_import_cpu_limit_is_capped_below_online_cpu_count(self):
        runtime = ROOT / "scripts" / "openmeteo_runtime_common.sh"
        command = f'''source "{runtime}"
getconf() {{ printf '2\\n'; }}
resolve_openmeteo_cpu_limit 2.5
'''
        completed = subprocess.run(
            ["bash", "-c", command],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.stdout.strip(), "1.5")

    def test_publishes_cams_coverage_without_webp_or_api(self):
        path = ROOT / "scripts" / "publish_native_cams_coverage.py"
        scripts_dir = str(path.parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        spec = importlib.util.spec_from_file_location("publish_native_cams_coverage", path)
        assert spec is not None
        assert spec.loader is not None
        publisher = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = publisher
        spec.loader.exec_module(publisher)

        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "producer"
            staging = make_cams_staging(output_root, "2026071300")
            ready = publisher.publish_cams_coverage(
                argparse.Namespace(
                    staging_dir=str(staging),
                    output_root=str(output_root),
                    run="2026071300",
                    source_runs="2026071200,2026071212,2026071300",
                    greenhouse_source_runs="2026070900,2026071000,2026071100",
                    latest_max_forecast_hour=120,
                    public_start_utc="2026-07-12T00:00:00Z",
                    public_end_utc="2026-07-18T00:00:00Z",
                    public_hours=144,
                    local_day_start_utc="2026-07-12T16:00:00Z",
                    keep_coverages=3,
                    required_variables="pm2_5,dust",
                    greenhouse_required_variables="carbon_monoxide",
                    coverage_revision=None,
                )
            )

            coverage = output_root / "coverages" / "cams" / "cams_native_2026071300"
            self.assertTrue((coverage / "coverage.json").is_file())
            self.assertEqual(ready["products"]["cams_global"]["runtime_domain"], "cams_global")
            self.assertEqual(
                ready["source_runs"],
                ["2026071200", "2026071212", "2026071300"],
            )
            self.assertEqual(ready["domain_grids"]["cams_global"]["nx"], 176)
            self.assertEqual(
                ready["products"]["cams_global"]["grid"],
                ready["domain_grids"]["cams_global"],
            )
            self.assertEqual(
                ready["products"]["cams_global_greenhouse_gases"]["runtime_domain"],
                "cams_global_greenhouse_gases",
            )
            self.assertEqual(
                ready["greenhouse_source_runs"],
                ["2026070900", "2026071000", "2026071100"],
            )
            self.assertEqual(ready["latest_max_forecast_hour"], 120)
            self.assertTrue((output_root / "current" / "cams").is_symlink())

        producer = (ROOT / "scripts" / "run_cams_om_production_cycle.sh").read_text(
            encoding="utf-8"
        )
        scheduler = (ROOT / "scripts" / "run_cams_ftp_scheduled_cycle.sh").read_text(
            encoding="utf-8"
        )
        pipeline = (ROOT / "scripts" / "run_native_model_pipeline.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("publish_native_cams_coverage.py", producer)
        self.assertIn("seed_native_om_staging.py", producer)
        self.assertIn('SOURCE_RUN_COUNT="${WEATHER_CAMS_REQUIRED_SOURCE_RUN_COUNT:-3}"', producer)
        self.assertIn('KEEP_COVERAGES="${WEATHER_OM_CAMS_KEEP_COVERAGES:-1}"', producer)
        self.assertIn("prune_native_om_runs.py", producer)
        self.assertIn("download_openmeteo_cams_greenhouse_data.sh", producer)
        self.assertIn('GREENHOUSE_SOURCE_RUN_COUNT="${WEATHER_CAMS_GREENHOUSE_SOURCE_RUN_COUNT:-3}"', producer)
        self.assertIn('WEATHER_CAMS_COVERAGE_REVISION:-greenhouse-region-v3', producer)
        self.assertIn('WEATHER_CAMS_FORCE_GREENHOUSE_DOWNLOAD:-false', producer)
        self.assertIn('is_truthy "$FORCE_GREENHOUSE_DOWNLOAD"', producer)
        self.assertIn('rm -rf -- "$STAGING_DIR/cams_global_greenhouse_gases"', producer)
        self.assertIn("run.replace(hour=0) - timedelta(days=2)", producer)
        self.assertIn('GREENHOUSE_LATEST_JSON.tmp.$$"', producer)
        self.assertIn('mv -f "$GREENHOUSE_LATEST_JSON.tmp.$$" "$GREENHOUSE_LATEST_JSON"', producer)
        self.assertNotIn("build_openmeteo_cams_layers.sh", producer)
        self.assertNotIn("build_webp.py", producer)
        self.assertNotIn("run_openmeteo_api_server.sh", producer)
        self.assertIn("run_native_model_pipeline.sh cams", scheduler)
        self.assertIn("run_cams_om_production_cycle.sh", pipeline)
        self.assertNotIn("run_cams_ftp_production_cycle.sh", scheduler)


if __name__ == "__main__":
    unittest.main()
