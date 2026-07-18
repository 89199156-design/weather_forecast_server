import unittest
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


class NativeModelPipelineTests(unittest.TestCase):
    def test_pipeline_is_event_driven_and_strictly_ordered(self):
        source = (ROOT / "scripts" / "run_native_model_pipeline.sh").read_text(encoding="utf-8")
        om = source.index("run_gfs_om_production_cycle.sh")
        nofile = source.index('ulimit -S -n "$WEBP_NOFILE_LIMIT"')
        webp = source.index("nice -n 10 ionice")
        signal = source.index("systemctl reload")
        prune = source.index("prune_native_coverage_history.py")

        self.assertLess(om, webp)
        self.assertLess(nofile, webp)
        self.assertLess(webp, signal)
        self.assertLess(signal, prune)
        self.assertIn("published_identity", source)
        self.assertIn('actual_run" != "$RUN', source)
        self.assertNotIn("sleep ", source)
        self.assertNotIn("while true", source)
        self.assertNotIn("find ", source)
        self.assertIn('--public-root "$WEBP_PUBLIC_ROOT"', source)
        self.assertIn('--workers "$WEBP_WORKERS"', source)
        self.assertIn('WEATHER_OM_WEBP_NOFILE_LIMIT:-65536', source)
        self.assertIn("--show-cursor", source)
        self.assertIn('--after-cursor="$journal_cursor"', source)
        self.assertIn("--follow", source)
        self.assertIn('if [[ "$reload_confirmed" == "true" ]]', source)
        self.assertIn("old coverage retained", source)

    def test_installer_retires_legacy_five_minute_webp_jobs(self):
        installer = (ROOT / "scripts" / "install_openmeteo_cron.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("OM_GFS_WEBP_BUILD", installer)
        self.assertIn("OM_CAMS_WEBP_BUILD", installer)
        self.assertNotIn("*/5", installer)
        for helper in (
            "install_1panel_jobs.py",
            "inspect_1panel_jobs.py",
            "run_scope.sh",
            "verify_deployment.py",
        ):
            self.assertFalse((ROOT / "om_webp" / "scripts" / helper).exists())
        webp_readme = (ROOT / "om_webp" / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("Shanghai", webp_readme)
        self.assertNotIn("five minutes", webp_readme)

    def test_schedulers_enter_one_pipeline_instead_of_polling_local_completion(self):
        gfs = (ROOT / "scripts" / "run_gfs_probe_and_cycle.sh").read_text(encoding="utf-8")
        cams = (ROOT / "scripts" / "run_cams_ftp_scheduled_cycle.sh").read_text(encoding="utf-8")

        self.assertIn("run_native_model_pipeline.sh gfs", gfs)
        self.assertIn("run_native_model_pipeline.sh cams", cams)
        self.assertNotIn("pgrep", gfs + cams)
        self.assertNotIn("docker ps", gfs + cams)
        self.assertNotIn("sleep ", gfs + cams)
        self.assertNotIn("while true", gfs + cams)
        self.assertNotIn("curl 127.0.0.1", gfs + cams)

    def test_cams_scheduler_selects_ready_line_after_newer_incomplete_run(self):
        cams = (ROOT / "scripts" / "run_cams_ftp_scheduled_cycle.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("ready_line=", cams)
        self.assertIn('$1 == "READY"', cams)
        self.assertIn('read -r ready_marker run ready_reference_time', cams)
        self.assertNotIn("set -- $probe_output", cams)

    def test_runbook_forbids_client_facing_refresh_polling(self):
        runbook = (ROOT / "docs" / "singapore-native-migration-runbook.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("same GFS/CAMS run must not be regenerated", runbook)
        self.assertIn("never scan the producer directory", runbook)
        self.assertIn("one API SIGHUP", runbook)
        self.assertIn("no separate high-frequency watcher", runbook)

    def test_successful_batch_executes_om_webp_and_one_reload_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            scripts = app / "scripts"
            scripts.mkdir(parents=True)
            producer = root / "producer"
            log = root / "events.log"
            decoder = root / "libomfileformat.so"
            decoder.write_bytes(b"test")
            (scripts / "openmeteo_runtime_common.sh").write_text(
                "load_weather_env() { :; }\n", encoding="utf-8"
            )
            om_stub = """#!/usr/bin/env bash
set -euo pipefail
scope=__SCOPE__
run="$1"
printf 'OM %s %s\n' "$scope" "$run" >> "$WEATHER_TEST_EVENT_LOG"
marker="$WEATHER_OM_PRODUCER_ROOT/groups/$scope/current/ready_for_processing.json"
mkdir -p "$(dirname "$marker")"
printf '{"status":"complete","runtime_format":"openmeteo-native-v1","latest_complete_run":"%s","coverage_id":"%s_native_%s"}\n' "$run" "$scope" "$run" > "$marker"
"""
            for scope in ("gfs", "cams"):
                path = scripts / f"run_{scope}_om_production_cycle.sh"
                path.write_text(om_stub.replace("__SCOPE__", scope), encoding="utf-8")
                path.chmod(0o755)
            webp = root / "om-webp"
            webp.write_text(
                "#!/usr/bin/env bash\nprintf 'WEBP %s\n' \"$*\" >> \"$WEATHER_TEST_EVENT_LOG\"\n",
                encoding="utf-8",
            )
            webp.chmod(0o755)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            systemctl = bin_dir / "systemctl"
            systemctl.write_text(
                "#!/usr/bin/env bash\nprintf 'SYSTEMCTL %s\n' \"$*\" >> \"$WEATHER_TEST_EVENT_LOG\"\nexit 0\n",
                encoding="utf-8",
            )
            systemctl.chmod(0o755)
            journalctl = bin_dir / "journalctl"
            journalctl.write_text(
                """#!/usr/bin/env bash
case " $* " in
  *" --show-cursor "*) printf '%s\n' '-- cursor: test-cursor' ;;
  *" --follow "*) printf '%s\n' 'published new immutable OM API snapshot' ;;
esac
""",
                encoding="utf-8",
            )
            journalctl.chmod(0o755)
            (scripts / "prune_native_coverage_history.py").write_text(
                """import os
import sys
with open(os.environ["WEATHER_TEST_EVENT_LOG"], "a", encoding="utf-8") as output:
    output.write("PRUNE " + " ".join(sys.argv[1:]) + "\\n")
""",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "WEATHER_FORECAST_APP_DIR": str(app),
                    "WEATHER_OM_PRODUCER_ROOT": str(producer),
                    "WEATHER_OM_PIPELINE_LOCK_FILE": str(root / "pipeline.lock"),
                    "WEATHER_OM_WEBP_BIN": str(webp),
                    "WEATHER_OM_WEBP_DATA_ROOT": str(root / "webp"),
                    "WEATHER_OM_WEBP_PUBLIC_ROOT": str(root / "public"),
                    "WEATHER_OMFILE_LIB": str(decoder),
                    "WEATHER_TEST_EVENT_LOG": str(log),
                    "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
                }
            )
            completed = subprocess.run(
                ["bash", str(ROOT / "scripts" / "run_native_model_pipeline.sh"), "gfs", "2026071300"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            events = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(events[0], "OM gfs 2026071300")
            self.assertTrue(events[1].startswith("WEBP --scope gfs"))
            self.assertIn(f"--public-root {root / 'public'}", events[1])
            self.assertIn("--workers 1", events[1])
            self.assertEqual(events[2], "SYSTEMCTL is-active --quiet weather-om-api.service")
            self.assertEqual(events[3], "SYSTEMCTL reload weather-om-api.service")
            self.assertIn("PRUNE --producer-root", events[4])
            self.assertIn("--scope gfs", events[4])
            self.assertIn("--expected-coverage-id gfs_native_2026071300", events[4])
            self.assertEqual(len(events), 5)


if __name__ == "__main__":
    unittest.main()
