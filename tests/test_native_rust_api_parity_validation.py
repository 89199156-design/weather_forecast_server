import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NativeRustApiParityValidationTests(unittest.TestCase):
    def test_runner_uses_real_service_and_enforces_full_acceptance_gate(self):
        source = (ROOT / "scripts" / "run_native_rust_api_parity_validation.sh").read_text(encoding="utf-8")
        self.assertIn("WEATHER_SINGAPORE_OM_API_URL", source)
        self.assertIn("WEATHER_OM_API_PID", source)
        self.assertIn('--process-pid "$API_PID"', source)
        self.assertIn("http://127.0.0.1:8088", source)
        self.assertIn("validate_native_om_coverage.py", source)
        self.assertIn("validate_native_cams_coverage.py", source)
        self.assertIn("compare_shanghai_singapore_api.py", source)
        self.assertIn("compare_shanghai_singapore_daily.py", source)
        self.assertIn("shanghai-singapore-2000-all-hours.json", source)
        self.assertIn("shanghai-singapore-2000x3-daily.json", source)
        self.assertIn(
            '--hourly-acceptance-report "$REPORT_ROOT/shanghai-singapore-2000-all-hours.json"',
            source,
        )
        self.assertIn("compare_model_run_identities.py", source)
        self.assertIn("singapore-model-run-identity.json", source)
        self.assertIn("WEATHER_OM_RUN_IDENTITY_REPORT", source)
        self.assertIn("WEATHER_OM_DAILY_RUN_IDENTITY_REPORT", source)
        self.assertIn('--run-identity-report "$HOURLY_RUN_IDENTITY_REPORT"', source)
        self.assertIn('--run-identity-report "$DAILY_RUN_IDENTITY_REPORT"', source)
        self.assertIn('"$DAILY_RUN_IDENTITY_REPORT" -ef "$HOURLY_RUN_IDENTITY_REPORT"', source)
        self.assertIn("compare_webp_inventories.py", source)
        self.assertIn("singapore-webp-inventory.json", source)
        self.assertIn("WEATHER_SHANGHAI_WEBP_INVENTORY", source)
        self.assertIn('--shanghai-inventory "$SHANGHAI_WEBP_INVENTORY"', source)
        self.assertIn('--singapore-inventory "$REPORT_ROOT/singapore-webp-inventory.json"', source)
        self.assertIn("shanghai-singapore-webp-exact.json", source)
        self.assertNotIn("--allow-reduced-test", source)
        self.assertNotIn("docker stop", source)
        self.assertNotIn("systemctl", source)
        self.assertNotIn("om-api-candidate", source)
        self.assertNotIn("WEATHER_OM_API_PARITY_PORT", source)

    def test_runner_requires_both_native_groups_and_uses_their_latest_runs(self):
        source = (ROOT / "scripts" / "run_native_rust_api_parity_validation.sh").read_text(encoding="utf-8")
        self.assertIn("for group in gfs cams", source)
        self.assertIn("GFS_RUN=", source)
        self.assertIn("CAMS_RUN=", source)
        self.assertIn("runtime_format", source)
        self.assertIn("latest_complete_run", source)


if __name__ == "__main__":
    unittest.main()
