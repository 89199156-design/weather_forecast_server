#!/usr/bin/env python3
"""Run Open-Meteo point API validation gates in 50 -> 100 -> 500 order."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


def build_gate_commands(
    *,
    api_base_url: str,
    reference_base_url: str | None,
    output_dir: Path,
    scopes: list[str],
    point_gates: list[int],
    frames: int,
    point_chunk_size: int,
    request_retries: int,
    request_retry_delay: float,
    request_pause: float,
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    validator = SCRIPT_DIR / "validate_openmeteo_point_api.py"
    for points in point_gates:
        for scope in scopes:
            report = output_dir / f"{points}x{frames}-{scope}.json"
            argv = [
                sys.executable,
                str(validator),
                "--api-base-url",
                api_base_url,
                "--scope",
                scope,
                "--points",
                str(points),
                "--frames",
                str(frames),
                "--point-chunk-size",
                str(point_chunk_size),
                "--request-retries",
                str(request_retries),
                "--request-retry-delay",
                str(request_retry_delay),
                "--request-pause",
                str(request_pause),
                "--output-report",
                str(report),
            ]
            if reference_base_url:
                argv.extend(["--reference-base-url", reference_base_url])
            commands.append({"points": points, "scope": scope, "frames": frames, "report": str(report), "argv": argv})
    return commands


def summarize_results(results: list[dict[str, Any]], *, planned: list[dict[str, Any]]) -> dict[str, Any]:
    failed_at = None
    for result in results:
        if result["exit_code"] != 0:
            failed_at = {"points": result["points"], "scope": result["scope"], "report": result["report"]}
            break

    skipped: list[dict[str, Any]] = []
    if failed_at is not None:
        completed_keys = {(result["points"], result["scope"], result["report"]) for result in results}
        skipped = [
            {"points": command["points"], "scope": command["scope"], "report": command["report"]}
            for command in planned
            if (command["points"], command["scope"], command["report"]) not in completed_keys
        ]

    return {
        "passed": failed_at is None,
        "failed_at": failed_at,
        "results": results,
        "skipped": skipped,
    }


def run_commands(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for command in commands:
        completed = subprocess.run(command["argv"], check=False)
        result = {
            "points": command["points"],
            "scope": command["scope"],
            "frames": command["frames"],
            "report": command["report"],
            "exit_code": completed.returncode,
        }
        results.append(result)
        if completed.returncode != 0:
            break
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Open-Meteo 50/100/500 point API validation gates.")
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--reference-base-url")
    parser.add_argument("--output-dir", default="docs/validation/reports")
    parser.add_argument("--scopes", default="gfs,cams")
    parser.add_argument("--point-gates", default="50,100,500")
    parser.add_argument("--frames", type=int, default=50)
    parser.add_argument("--point-chunk-size", type=int, default=10)
    parser.add_argument("--request-retries", type=int, default=3)
    parser.add_argument("--request-retry-delay", type=float, default=2.0)
    parser.add_argument("--request-pause", type=float, default=0.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scopes = [scope.strip() for scope in args.scopes.split(",") if scope.strip()]
    point_gates = [int(value.strip()) for value in args.point_gates.split(",") if value.strip()]
    commands = build_gate_commands(
        api_base_url=args.api_base_url,
        reference_base_url=args.reference_base_url,
        output_dir=output_dir,
        scopes=scopes,
        point_gates=point_gates,
        frames=args.frames,
        point_chunk_size=args.point_chunk_size,
        request_retries=args.request_retries,
        request_retry_delay=args.request_retry_delay,
        request_pause=args.request_pause,
    )
    results = run_commands(commands)
    summary = summarize_results(results, planned=commands)
    summary_path = output_dir / f"summary-{args.frames}frames.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": summary["passed"], "summary": str(summary_path)}))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
