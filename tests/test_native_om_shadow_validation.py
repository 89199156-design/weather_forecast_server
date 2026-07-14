from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeOmShadowValidationTests(unittest.TestCase):
    def test_shadow_api_is_isolated_read_only_and_ephemeral(self):
        script = (ROOT / "scripts" / "run_native_om_shadow_validation.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('--publish "127.0.0.1:$SHADOW_PORT:8080"', script)
        self.assertIn("--read-only", script)
        self.assertIn("--cap-drop ALL", script)
        self.assertIn("--security-opt no-new-privileges", script)
        self.assertIn("dst=/app/data,readonly", script)
        self.assertIn("docker run -d --rm", script)
        self.assertIn("validate_native_om_coverage.py", script)
        self.assertIn("validate_native_cams_coverage.py", script)
        self.assertIn('SCOPE="${1:-gfs}"', script)
        self.assertIn("Refusing to replace existing container", script)
        self.assertNotIn("docker rm -f", script)
        self.assertNotIn("--restart", script)

    def test_shadow_image_defaults_to_exact_production_container_image(self):
        script = (ROOT / "scripts" / "run_native_om_shadow_validation.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("WEATHER_OPENMETEO_SHADOW_IMAGE", script)
        self.assertIn("docker container inspect --format '{{.Config.Image}}'", script)


if __name__ == "__main__":
    unittest.main()
