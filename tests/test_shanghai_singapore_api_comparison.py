import importlib.util
import json
import time
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compare_shanghai_singapore_api.py"
spec = importlib.util.spec_from_file_location("compare_api", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ShanghaiSingaporeApiComparisonTests(unittest.TestCase):
    def test_default_contract_has_22_pressure_levels_and_both_products(self):
        gfs = module.variables_for_scope("gfs")
        cams = module.variables_for_scope("cams")
        self.assertEqual(len(module.PRESSURE_LEVELS), 22)
        self.assertEqual(len(gfs), 186)
        self.assertEqual(len(cams), 39)
        self.assertEqual(module.full_hours_for_scope("gfs", "2026071312"), 381)
        self.assertEqual(module.full_hours_for_scope("gfs", "2026071300"), 393)
        self.assertEqual(module.full_hours_for_scope("gfs", "2026071318"), 387)
        self.assertEqual(module.full_hours_for_scope("cams", "2026071300"), 121)
        cams_direct_values_per_point = sum(
            len(module.direct_source_hour_indices("cams", variable, "2026071300", 121))
            for variable in cams
        )
        cams_strict_values_per_point = sum(
            len(module.strict_comparison_hour_indices("cams", variable, "2026071300", 121))
            for variable in cams
        )
        cams_waiver_values_per_point = (
            len(module.CAMS_EXPECTED_SEMANTIC_DIFFERENCE_VARIABLES) * 121
        )
        cams_interpolation_only_values_per_point = sum(
            121 - len(module.direct_source_hour_indices("cams", variable, "2026071300", 121))
            for variable in cams
            if variable not in module.CAMS_EXPECTED_SEMANTIC_DIFFERENCE_VARIABLES
        )
        self.assertEqual(cams_direct_values_per_point, 2_319)
        self.assertEqual(cams_strict_values_per_point, 2_032)
        self.assertEqual(cams_waiver_values_per_point, 847)
        self.assertEqual(cams_interpolation_only_values_per_point, 1_840)
        self.assertEqual(
            cams_strict_values_per_point
            + cams_waiver_values_per_point
            + cams_interpolation_only_values_per_point,
            121 * len(cams),
        )
        self.assertEqual(2000 * (381 * len(gfs) + cams_strict_values_per_point), 145_796_000)
        self.assertIn("apparent_temperature", gfs)
        self.assertIn("uv_index_clear_sky", gfs)
        self.assertIn("temperature_975hPa", gfs)
        self.assertIn("vertical_velocity_50hPa", gfs)
        self.assertIn("chinese_aqi", cams)

    def test_every_cams_variable_has_an_explicit_direct_source_cadence(self):
        variables = module.variables_for_scope("cams")
        cadence = {
            variable: module.direct_source_cadence_hours("cams", variable)
            for variable in variables
        }

        self.assertEqual(set(cadence.values()), {1, 3})
        self.assertEqual(sum(value == 1 for value in cadence.values()), 9)
        self.assertEqual(sum(value == 3 for value in cadence.values()), 30)
        self.assertEqual(cadence["pm2_5"], 1)
        self.assertEqual(cadence["dust"], 3)
        self.assertEqual(cadence["carbon_monoxide"], 3)
        self.assertEqual(cadence["us_aqi"], 3)
        self.assertEqual(
            module.CAMS_EXPECTED_SEMANTIC_DIFFERENCE_VARIABLES,
            {
                "us_aqi", "us_aqi_o3", "us_aqi_ozone", "us_aqi_so2",
                "us_aqi_sulphur_dioxide", "us_aqi_co", "us_aqi_carbon_monoxide",
            },
        )

    def test_cams_three_hour_source_compares_only_run_aligned_direct_hours(self):
        self.assertEqual(
            module.direct_source_hour_indices(
                "cams", "dust", "2026071300", 121
            ),
            list(range(0, 121, 3)),
        )
        self.assertEqual(
            module.direct_source_hour_indices(
                "cams", "pm2_5", "2026071300", 5
            ),
            [0, 1, 2, 3, 4],
        )
        self.assertEqual(
            module.strict_comparison_hour_indices(
                "cams", "us_aqi_o3", "2026071300", 121
            ),
            [],
        )

    def test_comparable_payload_filters_only_cams_interpolation_values(self):
        payload = {
            "generationtime_ms": 1.0,
            "hourly_units": {"time": "iso8601", "pm2_5": "ug/m3", "dust": "ug/m3"},
            "hourly": {
                "time": ["h0", "h1", "h2", "h3"],
                "pm2_5": [10.0, 11.0, 12.0, 13.0],
                "dust": [20.0, 21.0, 22.0, 23.0],
            },
        }
        indices = {
            variable: module.direct_source_hour_indices(
                "cams", variable, "2026071300", 4
            )
            for variable in ("pm2_5", "dust")
        }

        filtered = module.comparable_payload(payload, ["pm2_5", "dust"], indices)

        self.assertEqual(filtered["hourly"]["time"], ["h0", "h1", "h2", "h3"])
        self.assertEqual(filtered["hourly"]["pm2_5"], [10.0, 11.0, 12.0, 13.0])
        self.assertEqual(filtered["hourly"]["dust"], [20.0, 23.0])
        self.assertEqual(filtered["hourly_units"], payload["hourly_units"])

    def test_cams_job_digest_and_value_count_use_only_direct_source_hours(self):
        times = [f"2026-07-13T0{hour}:00" for hour in range(4)]
        base = {
            "latitude": 31.2,
            "longitude": 121.5,
            "hourly_units": {"time": "iso8601", "pm2_5": "ug/m3", "dust": "ug/m3"},
            "hourly": {
                "time": times,
                "pm2_5": [10.0, 11.0, 12.0, 13.0],
                "dust": [20.0, 21.0, 22.0, 23.0],
            },
        }
        singapore = json.loads(json.dumps(base))
        singapore["hourly"]["dust"][1:3] = [210.0, 220.0]
        job = {
            "job_id": "cams-p0000-v000",
            "scope": "cams",
            "run": "2026071300",
            "hours": 4,
            "points": [{"latitude": 31.2, "longitude": 121.5}],
            "variables": ["pm2_5", "dust"],
        }

        with patch.object(module, "fetch", side_effect=[base, singapore]):
            result = module.compare_job_unthrottled(job, "http://shanghai", "http://singapore", 1.0)

        self.assertTrue(result["equal"])
        self.assertEqual(result["values"], 6)
        self.assertEqual(result["excluded_interpolated_values"], 2)

    def test_rolling_aqi_difference_is_observed_and_reported_but_not_gated(self):
        times = [f"2026-07-13T0{hour}:00" for hour in range(4)]
        shanghai = {
            "latitude": 31.2,
            "longitude": 121.5,
            "hourly_units": {"time": "iso8601", "us_aqi_o3": "US AQI"},
            "hourly": {"time": times, "us_aqi_o3": [1.0, 2.0, 3.0, 4.0]},
        }
        singapore = json.loads(json.dumps(shanghai))
        singapore["hourly"]["us_aqi_o3"] = [11.0, 12.0, 13.0, 14.0]
        job = {
            "job_id": "cams-p0000-v000",
            "scope": "cams",
            "run": "2026071300",
            "hours": 4,
            "points": [{"latitude": 31.2, "longitude": 121.5}],
            "variables": ["us_aqi_o3"],
        }

        with patch.object(module, "fetch", side_effect=[shanghai, singapore]):
            result = module.compare_job_unthrottled(job, "http://shanghai", "http://singapore", 1.0)

        self.assertTrue(result["equal"])
        self.assertEqual(result["values"], 0)
        self.assertEqual(result["excluded_interpolated_values"], 0)
        self.assertEqual(result["semantic_waiver_values"], 4)
        self.assertEqual(result["semantic_waiver_mismatches"], 4)
        waiver = result["expected_semantic_differences"]
        self.assertEqual(waiver["variables"], ["us_aqi_o3"])
        self.assertEqual(waiver["by_variable"]["us_aqi_o3"]["mismatched_values"], 4)

    def test_random_points_are_reproducible_unique_and_inside_region(self):
        left, right, bottom, top = (70.0, 140.0, 0.0, 58.0)
        first = module.random_points(2000, 20260713, (left, right, bottom, top))
        second = module.random_points(2000, 20260713, (left, right, bottom, top))
        self.assertEqual(first, second)
        self.assertEqual(len({(point["latitude"], point["longitude"]) for point in first}), 2000)
        self.assertTrue(all(bottom <= point["latitude"] <= top and left <= point["longitude"] <= right for point in first))

    def test_gfs_request_is_24_hours_from_utc8_local_day_midnight(self):
        path = module.request_path("gfs", [{"latitude": 31.2, "longitude": 121.5}], ["temperature_2m"], "2026071300", 24)
        self.assertIn("start_hour=2026-07-12T16%3A00", path)
        self.assertIn("end_hour=2026-07-13T15%3A00", path)

        noon = module.request_path("gfs", [{"latitude": 31.2, "longitude": 121.5}], ["temperature_2m"], "2026071312", 24)
        self.assertIn("start_hour=2026-07-13T16%3A00", noon)
        self.assertIn("end_hour=2026-07-14T15%3A00", noon)

        evening = module.request_path("gfs", [{"latitude": 31.2, "longitude": 121.5}], ["temperature_2m"], "2026071318", 24)
        self.assertIn("start_hour=2026-07-13T16%3A00", evening)
        self.assertIn("end_hour=2026-07-14T15%3A00", evening)

    def test_gfs_probe_uses_actual_axis_intersection_when_shanghai_starts_later(self):
        run = "2026071406"
        nominal_start = module.comparison_start("gfs", run)
        nominal_end = module.parse_run(run) + timedelta(hours=384)

        def payload(start):
            hours = int((nominal_end - start).total_seconds() // 3600) + 1
            times = [
                module.format_hour(start + timedelta(hours=index))
                for index in range(hours)
            ]
            return {
                "hourly": {
                    "time": times,
                    "temperature_2m": [20.0] * hours,
                }
            }

        shanghai = payload(nominal_start + timedelta(hours=2))
        singapore = payload(nominal_start)
        with patch.object(module, "fetch", side_effect=[shanghai, singapore]) as mocked:
            window = module.discover_shared_gfs_window(
                "http://shanghai",
                "http://singapore",
                {"latitude": 16.7, "longitude": 132.7},
                run,
                1.0,
            )

        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(window["reason"], "actual_shared_window")
        self.assertEqual(window["run"], run)
        self.assertEqual(window["start_utc"], "2026-07-13T18:00")
        self.assertEqual(window["end_utc"], module.format_hour(nominal_end))
        self.assertEqual(window["hours"], 397)
        self.assertEqual(window["nominal_start"], "2026-07-13T16:00")
        self.assertEqual(window["shanghai"]["start"], "2026-07-13T18:00")
        self.assertEqual(window["singapore"]["start"], "2026-07-13T16:00")
        self.assertEqual(window["shared_start"], "2026-07-13T18:00")
        self.assertEqual(window["shared_end"], module.format_hour(nominal_end))
        self.assertEqual(window["shared_hours"], 397)

    def test_gfs_reduced_and_full_windows_both_begin_at_shared_start(self):
        shared = {
            "shared_start": "2026-07-13T18:00",
            "shared_end": "2026-07-30T06:00",
            "shared_hours": 397,
        }
        expected_start = datetime(2026, 7, 13, 18, tzinfo=timezone.utc)

        full_start, full_hours = module.select_gfs_comparison_window(
            shared, None, require_acceptance_minimum=True
        )
        reduced_start, reduced_hours = module.select_gfs_comparison_window(
            shared, 24, require_acceptance_minimum=False
        )

        self.assertEqual((full_start, full_hours), (expected_start, 397))
        self.assertEqual((reduced_start, reduced_hours), (expected_start, 24))
        reduced_path = module.request_path(
            "gfs",
            [{"latitude": 31.2, "longitude": 121.5}],
            ["temperature_2m"],
            "2026071406",
            reduced_hours,
            start=reduced_start,
        )
        self.assertIn("start_hour=2026-07-13T18%3A00", reduced_path)
        self.assertIn("end_hour=2026-07-14T17%3A00", reduced_path)

        too_short = dict(shared, shared_hours=299)
        with self.assertRaisesRegex(ValueError, "at least 300 shared hours"):
            module.select_gfs_comparison_window(
                too_short, None, require_acceptance_minimum=True
            )
        with self.assertRaisesRegex(ValueError, "exceed"):
            module.select_gfs_comparison_window(
                shared, 398, require_acceptance_minimum=False
            )

    def test_gfs_job_requests_and_validates_the_discovered_shared_start(self):
        start = datetime(2026, 7, 13, 18, tzinfo=timezone.utc)
        times = [module.format_hour(start + timedelta(hours=index)) for index in range(3)]
        payload = {
            "latitude": 31.2,
            "longitude": 121.5,
            "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
            "hourly": {"time": times, "temperature_2m": [20.0, 21.0, 22.0]},
        }
        job = {
            "job_id": "gfs-p0000-v000",
            "scope": "gfs",
            "run": "2026071406",
            "start": start,
            "hours": 3,
            "points": [{"latitude": 31.2, "longitude": 121.5}],
            "variables": ["temperature_2m"],
        }

        with patch.object(module, "fetch", side_effect=[payload, payload]) as mocked:
            result = module.compare_job_unthrottled(
                job, "http://shanghai", "http://singapore", 1.0
            )

        self.assertTrue(result["equal"])
        requested_path = mocked.call_args_list[0].args[1]
        self.assertIn("start_hour=2026-07-13T18%3A00", requested_path)
        self.assertIn("end_hour=2026-07-13T20%3A00", requested_path)

    def test_gfs_single_batch_boundary_allows_only_shanghai_history_fill(self):
        start = datetime(2026, 7, 13, 18, tzinfo=timezone.utc)
        times = [module.format_hour(start + timedelta(hours=index)) for index in range(2)]
        shanghai = {
            "latitude": 31.2,
            "longitude": 121.5,
            "hourly_units": {
                "time": "iso8601",
                "cloud_cover": "%",
                "weather_code": "wmo code",
            },
            "hourly": {
                "time": times,
                "cloud_cover": [80, 10],
                "weather_code": [3, 0],
            },
        }
        singapore = json.loads(json.dumps(shanghai))
        singapore["hourly"]["cloud_cover"][0] = None
        singapore["hourly"]["weather_code"][0] = None
        job = {
            "job_id": "gfs-p0000-v000",
            "scope": "gfs",
            "run": "2026071406",
            "start": start,
            "hours": 2,
            "points": [{"latitude": 31.2, "longitude": 121.5}],
            "variables": ["cloud_cover", "weather_code"],
        }

        with patch.object(module, "fetch", side_effect=[shanghai, singapore]):
            result = module.compare_job_unthrottled(
                job, "http://shanghai", "http://singapore", 1.0
            )

        self.assertTrue(result["equal"])
        self.assertEqual(result["values"], 2)
        self.assertEqual(result["gfs_single_batch_boundary_values_excluded"], 2)
        self.assertEqual(
            result["gfs_single_batch_boundary_differences"]["by_variable"],
            {"cloud_cover": 1, "weather_code": 1},
        )

    def test_gfs_boundary_does_not_hide_inverse_nonboundary_or_numeric_mismatch(self):
        start = datetime(2026, 7, 13, 18, tzinfo=timezone.utc)
        times = [module.format_hour(start + timedelta(hours=index)) for index in range(2)]
        base = {
            "latitude": 31.2,
            "longitude": 121.5,
            "hourly_units": {"time": "iso8601", "cloud_cover": "%"},
            "hourly": {"time": times, "cloud_cover": [None, 10]},
        }
        job = {
            "job_id": "gfs-p0000-v000",
            "scope": "gfs",
            "run": "2026071406",
            "start": start,
            "hours": 2,
            "points": [{"latitude": 31.2, "longitude": 121.5}],
            "variables": ["cloud_cover"],
        }

        inverse = json.loads(json.dumps(base))
        inverse["hourly"]["cloud_cover"] = [80, None]
        with patch.object(module, "fetch", side_effect=[base, inverse]):
            result = module.compare_job_unthrottled(
                job, "http://shanghai", "http://singapore", 1.0
            )
        self.assertFalse(result["equal"])
        self.assertEqual(result["gfs_single_batch_boundary_values_excluded"], 0)

        shanghai = json.loads(json.dumps(base))
        singapore = json.loads(json.dumps(base))
        shanghai["hourly"]["cloud_cover"] = [80, 10]
        singapore["hourly"]["cloud_cover"] = [70, 10]
        with patch.object(module, "fetch", side_effect=[shanghai, singapore]):
            result = module.compare_job_unthrottled(
                job, "http://shanghai", "http://singapore", 1.0
            )
        self.assertFalse(result["equal"])
        self.assertEqual(result["gfs_single_batch_boundary_values_excluded"], 0)

    def test_gfs_probe_rejects_non_contiguous_endpoint_axis(self):
        payload = {
            "hourly": {
                "time": ["2026-07-13T16:00", "2026-07-13T18:00"],
                "temperature_2m": [20.0, 21.0],
            }
        }
        with self.assertRaisesRegex(ValueError, "strictly contiguous hourly"):
            module.parse_hour_axis(payload, "shanghai")

    def test_only_generation_time_is_excluded_from_strict_comparison(self):
        left = {"generationtime_ms": 1.0, "hourly": {"time": ["x"], "v": [1.0]}}
        right = {"generationtime_ms": 9.0, "hourly": {"time": ["x"], "v": [1.0]}}
        self.assertEqual(module.canonical_bytes(left), module.canonical_bytes(right))
        right["hourly"]["v"] = [1]
        self.assertNotEqual(module.canonical_bytes(left), module.canonical_bytes(right))

    def test_field_diagnostics_preserve_numeric_type_and_count_each_hour(self):
        left = {
            "generationtime_ms": 1.0,
            "latitude": 31.0,
            "hourly_units": {"time": "iso8601", "v": "m"},
            "hourly": {"time": ["a", "b"], "v": [1.0, 2.0]},
        }
        right = {
            "generationtime_ms": 9.0,
            "latitude": 31,
            "hourly_units": {"time": "iso8601", "v": "cm"},
            "hourly": {"time": ["a", "b"], "v": [1, 3.0]},
        }

        summary = module.field_mismatch_summary(left, right, ["v"])

        self.assertEqual(summary["counts"]["metadata"], {"latitude": 1})
        self.assertEqual(summary["counts"]["hourly_units"], {"v": 1})
        self.assertEqual(summary["counts"]["hourly_values"], {"v": 2})

    def test_cams_diagnostics_ignore_interpolated_hours_but_keep_direct_hours(self):
        left = {
            "hourly_units": {"time": "iso8601", "dust": "ug/m3"},
            "hourly": {"time": ["h0", "h1", "h2", "h3"], "dust": [1.0, 2.0, 3.0, 4.0]},
        }
        right = {
            "hourly_units": {"time": "iso8601", "dust": "ug/m3"},
            "hourly": {"time": ["h0", "h1", "h2", "h3"], "dust": [1.0, 20.0, 30.0, 5.0]},
        }
        indices = {"dust": [0, 3]}

        summary = module.field_mismatch_summary(
            left,
            right,
            ["dust"],
            hour_indices_by_variable=indices,
        )

        self.assertEqual(summary["counts"]["hourly_values"], {"dust": 1})
        self.assertEqual(summary["examples"][0]["hour"], 3)

    def test_payload_requires_every_hour_and_variable(self):
        run = module.comparison_start("gfs", "2026071300")
        times = [(run + timedelta(hours=index)).strftime("%Y-%m-%dT%H:00") for index in range(24)]
        payload = {"hourly": {"time": times, "temperature_2m": [1.0] * 24}}
        module.validate_payload(payload, "gfs", 1, ["temperature_2m"], "2026071300", 24)
        with self.assertRaises(ValueError):
            module.validate_payload(payload, "gfs", 1, ["temperature_2m", "cloud_cover"], "2026071300", 24)

    def test_acceptance_cli_defaults_are_throttled_for_live_shanghai(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--workers", type=int, default=1)', source)
        self.assertIn('parser.add_argument("--request-pause", type=float, default=0.2)', source)

    def test_api_gate_requires_passed_identity_report_for_exact_runs(self):
        with __import__("tempfile").TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            path.write_text(
                json.dumps(
                    {
                        "passed": True,
                        "same_source_runs": True,
                        "matched_latest_runs": {"gfs": "2026071300", "cams": "2026071212"},
                        "compared_at": int(time.time()),
                        "inventory_collected_at": {
                            "shanghai": int(time.time()),
                            "singapore": int(time.time()),
                        },
                    }
                ),
                encoding="utf-8",
            )
            module.validate_run_identity_report(path, "2026071300", "2026071212")
            with self.assertRaisesRegex(ValueError, "does not match"):
                module.validate_run_identity_report(path, "2026071306", "2026071212")

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["inventory_collected_at"]["shanghai"] = int(time.time()) - 901
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                module.validate_run_identity_report(path, "2026071300", "2026071212")


if __name__ == "__main__":
    unittest.main()
