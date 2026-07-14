#!/usr/bin/env python3
"""Hard-link a safe older native coverage into a new staging directory."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def safe_current_coverage(output_root: Path, group: str) -> tuple[Path, dict[str, Any]] | None:
    marker_path = output_root / "groups" / group / "current" / "ready_for_processing.json"
    current = output_root / "current" / group
    if not marker_path.is_file() or not current.is_symlink():
        return None
    marker = load_json(marker_path)
    if marker.get("status") != "complete" or marker.get("runtime_format") != "openmeteo-native-v1":
        return None
    relative_value = marker.get("coverage_path")
    if not isinstance(relative_value, str):
        return None
    relative = PurePosixPath(relative_value)
    if relative.is_absolute() or ".." in relative.parts or relative.parts[:2] != ("coverages", group):
        raise ValueError(f"unsafe coverage_path: {relative_value}")
    coverage = (output_root / Path(*relative.parts)).resolve(strict=True)
    expected_parent = (output_root / "coverages" / group).resolve(strict=True)
    if coverage.parent != expected_parent or current.resolve(strict=True) != coverage:
        raise ValueError("current coverage pointer and ready marker do not match")
    return coverage, marker


def coverage_data_stats(coverage: Path) -> tuple[int, int]:
    files = 0
    bytes_total = 0
    for path in coverage.rglob("*"):
        if not path.is_file() or path.is_symlink() or path == coverage / "coverage.json":
            continue
        files += 1
        bytes_total += path.stat().st_size
    return files, bytes_total


def seed_staging(
    output_root: Path,
    staging: Path,
    group: str,
    desired_source_runs: list[str],
) -> dict[str, Any]:
    output_root = output_root.resolve()
    expected_parent = (output_root / "staging").resolve()
    if staging.resolve().parent != expected_parent:
        raise ValueError(f"staging directory must be directly under {expected_parent}")
    if staging.exists():
        raise ValueError(f"staging directory already exists: {staging}")
    if not desired_source_runs:
        raise ValueError("desired_source_runs must not be empty")

    current = safe_current_coverage(output_root, group)
    reused: list[str] = []
    seeded_from: str | None = None
    seeded_latest_complete_run: str | None = None
    if current is not None:
        coverage, marker = current
        current_latest = str(marker.get("latest_complete_run") or "")
        desired_latest = desired_source_runs[-1]
        actual_files, actual_bytes = coverage_data_stats(coverage)
        marker_is_intact = (
            marker.get("files") == actual_files
            and marker.get("bytes") == actual_bytes
        )
        if current_latest and current_latest <= desired_latest and marker_is_intact:
            shutil.copytree(coverage, staging, copy_function=os.link, symlinks=True)
            (staging / "coverage.json").unlink(missing_ok=True)
            seeded_from = str(coverage)
            seeded_latest_complete_run = current_latest
            existing = marker.get("source_runs")
            if isinstance(existing, list):
                reused = [run for run in desired_source_runs if run in existing]

    if seeded_from is None:
        staging.mkdir(parents=True)
    return {
        "seeded_from": seeded_from,
        "seeded_latest_complete_run": seeded_latest_complete_run,
        "reused_source_runs": reused,
        "seed_rejected_reason": (
            "coverage_size_mismatch"
            if current is not None and seeded_from is None and not marker_is_intact
            else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--staging-dir", required=True)
    parser.add_argument("--group", choices=("gfs", "cams"), required=True)
    parser.add_argument("--source-runs", required=True)
    args = parser.parse_args()
    try:
        result = seed_staging(
            Path(args.output_root),
            Path(args.staging_dir),
            args.group,
            [item for item in args.source_runs.split(",") if item],
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
