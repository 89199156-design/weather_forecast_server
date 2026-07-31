#!/usr/bin/env python3
"""Validate and atomically publish regional ECMWF native full-run OM data."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any

from ecmwf_contract import (
    COMPLETE_RUN_RETENTION,
    GUST_SUPPORT_MAX_FORECAST_HOUR,
    GUST_SUPPORT_RUN_RETENTION,
    MODEL,
    OPENMETEO_UPSTREAM_COMMIT,
    RAW_VARIABLES_OMIT_HOUR_ZERO,
    SHORT_RUN_RETENTION,
    STORAGE_BOUNDS,
    parse_run,
    raw_variables_for_horizon,
    source_run_plan,
)
from om_v3_metadata import read_array_dimensions


ENSEMBLE_MODEL = "ecmwf_ifs025_ensemble"
PROBABILITY_VARIABLE = "precipitation_probability"
WIND_GUST_VARIABLE = "wind_gusts_10m"
UTC = timezone.utc


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.tmp.{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, link)


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def run_directory(staging: Path, domain: str, run: str) -> Path:
    parsed = parse_run(run)
    return staging / "data_run" / domain / parsed.strftime("%Y/%m/%d/%H00Z")


def expected_forecast_hours(
    run: str,
    horizon: int,
    *,
    include_hour_zero: bool = True,
) -> list[int]:
    parsed = parse_run(run)
    if horizon < 0:
        raise ValueError("ECMWF horizon must not be negative")
    if horizon == GUST_SUPPORT_MAX_FORECAST_HOUR:
        schedule = [
            *range(3, min(90, horizon) + 1, 3),
            *range(150, horizon + 1, 6),
        ]
    elif parsed.hour in (0, 12):
        schedule = [
            *range(0, min(144, horizon) + 1, 3),
            *range(150, horizon + 1, 6),
        ]
    else:
        schedule = list(range(0, min(144, horizon) + 1, 3))
    if not schedule or schedule[-1] != horizon:
        raise ValueError(f"unsupported ECMWF run horizon: run={run} horizon={horizon}")
    if not include_hour_zero and schedule[0] == 0:
        schedule = schedule[1:]
    return schedule


def ensemble_source_run_plan(target_run: str) -> tuple[tuple[str, int], ...]:
    target = parse_run(target_run)
    if target.hour not in (0, 12):
        raise ValueError("ECMWF target must be a 00Z or 12Z long run")
    return tuple(
        (
            (target - timedelta(hours=offset)).strftime("%Y%m%d%H"),
            360 if (target - timedelta(hours=offset)).hour in (0, 12) else 144,
        )
        for offset in (24, 18, 12, 6, 0)
    )


def validate_run(
    staging: Path,
    domain: str,
    run: str,
    horizon: int,
    required_variables: set[str],
) -> dict[str, Any]:
    directory = run_directory(staging, domain, run)
    meta_path = directory / "meta.json"
    if not meta_path.is_file():
        raise ValueError(f"missing ECMWF native run metadata: {meta_path}")
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    reference = parse_run(run)
    if parse_iso(str(payload.get("reference_time"))) != reference:
        raise ValueError(f"{domain} run {run} reference time mismatch")
    expected_times = [
        reference + timedelta(hours=hour)
        for hour in expected_forecast_hours(
            run,
            horizon,
            include_hour_zero=domain != ENSEMBLE_MODEL,
        )
    ]
    actual_times = [parse_iso(str(value)) for value in payload.get("valid_times") or []]
    if actual_times != expected_times:
        raise ValueError(f"{domain} run {run} valid-time schedule mismatch")
    variables = set(payload.get("variables") or [])
    missing = sorted(required_variables - variables)
    if missing:
        raise ValueError(
            f"{domain} run {run} is missing variables: {','.join(missing[:20])}"
        )
    for variable in required_variables:
        file_path = directory / f"{variable}.om"
        if not file_path.is_file() or file_path.stat().st_size <= 0:
            raise ValueError(f"missing ECMWF native file: {file_path}")
        dimensions = read_array_dimensions(file_path)
        if domain == MODEL and variable == WIND_GUST_VARIABLE:
            expected_time_count = sum(
                hour > 0 and (hour <= 90 or hour >= 150)
                for hour in expected_forecast_hours(run, horizon)
            )
        else:
            expected_time_count = len(expected_times)
        if (
            domain == MODEL
            and variable != WIND_GUST_VARIABLE
            and variable in RAW_VARIABLES_OMIT_HOUR_ZERO
            and expected_times[0] == reference
        ):
            expected_time_count -= 1
        expected_dimensions = (249, 297, expected_time_count)
        if dimensions != expected_dimensions:
            raise ValueError(
                f"{domain} {run} {variable} dimensions={dimensions}, "
                f"expected={expected_dimensions}"
            )
    return payload


def validate_static(staging: Path) -> None:
    elevation = staging / MODEL / "static" / "HSURF.om"
    if not elevation.is_file() or elevation.stat().st_size <= 0:
        raise ValueError("regional ECMWF HSURF.om is missing")
    if read_array_dimensions(elevation) != (249, 297):
        raise ValueError("regional ECMWF HSURF.om dimensions are invalid")


def directory_stats(root: Path) -> tuple[int, int]:
    files = 0
    bytes_total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            files += 1
            bytes_total += path.stat().st_size
    return files, bytes_total


def coverage_manifests(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    output: list[tuple[Path, dict[str, Any]]] = []
    if not root.exists():
        return output
    for directory in root.iterdir():
        path = directory / "coverage.json"
        if not directory.is_dir() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") == "complete":
            output.append((directory, payload))
    output.sort(
        key=lambda item: (
            str(item[1].get("latest_complete_run") or ""),
            str(item[1].get("generated_at") or ""),
        ),
        reverse=True,
    )
    return output


def protected_coverage_ids(root: Path) -> set[str]:
    protected: set[str] = set()
    for marker in (
        root / "groups" / "ecmwf" / "current" / "ready_for_processing.json",
        root / "groups" / "ecmwf" / "applied" / "current.json",
    ):
        if not marker.is_file():
            continue
        payload = json.loads(marker.read_text(encoding="utf-8"))
        coverage_id = str(payload.get("coverage_id") or "")
        if coverage_id:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", coverage_id):
                raise ValueError("unsafe protected ECMWF coverage identity")
            protected.add(coverage_id)
    return protected


def prune_coverages(root: Path, keep: int, current_id: str) -> None:
    if keep < 2:
        raise ValueError("ECMWF native retention must keep at least two coverages")
    retained = {current_id, *protected_coverage_ids(root)}
    manifests = coverage_manifests(root / "coverages" / "ecmwf")
    for directory, payload in manifests:
        coverage_id = str(payload.get("coverage_id") or directory.name)
        if len(retained) < keep:
            retained.add(coverage_id)
    parent = (root / "coverages" / "ecmwf").resolve()
    for directory, payload in manifests:
        coverage_id = str(payload.get("coverage_id") or directory.name)
        resolved = directory.resolve()
        if resolved.parent != parent:
            raise ValueError("ECMWF coverage resolves outside its managed root")
        if coverage_id not in retained:
            shutil.rmtree(resolved)


def grid_contract() -> dict[str, Any]:
    return {
        "grid_type": "regional_regular_lat_lon",
        "full_nx": 1440,
        "full_ny": 721,
        "x0": 992,
        "y0": 352,
        "nx": 297,
        "ny": 249,
        "dx": 0.25,
        "dy": 0.25,
        "lon_min": STORAGE_BOUNDS[0],
        "lat_min": STORAGE_BOUNDS[2],
        "dt_seconds": 3 * 3600,
        "om_file_length": 104,
    }


def publish(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).resolve()
    staging = Path(args.staging).resolve()
    expected_parent = (root / "staging").resolve()
    if staging.parent != expected_parent or not staging.is_dir():
        raise ValueError("ECMWF native staging is missing or outside its managed root")
    if not re.fullmatch(r"[a-f0-9]{64}", args.patch_sha256):
        raise ValueError("ECMWF patch SHA-256 is invalid")
    if not re.fullmatch(r"[a-f0-9]{40}", args.source_revision):
        raise ValueError("source revision must be a full lowercase Git commit")

    deterministic_plan = source_run_plan(args.run)
    ensemble_plan = ensemble_source_run_plan(args.run)
    for source_run, horizon in deterministic_plan:
        validate_run(
            staging,
            MODEL,
            source_run,
            horizon,
            set(raw_variables_for_horizon(horizon)),
        )
    for source_run, horizon in ensemble_plan:
        validate_run(
            staging,
            ENSEMBLE_MODEL,
            source_run,
            horizon,
            {PROBABILITY_VARIABLE},
        )
    validate_static(staging)
    for transient in (
        "http_cache",
        f"download-{MODEL}",
        f"download-{ENSEMBLE_MODEL}",
        MODEL,
        ENSEMBLE_MODEL,
    ):
        if transient == MODEL:
            continue
        path = staging / transient
        if path.exists():
            raise ValueError(f"ECMWF native staging retains transient data: {path}")

    coverage_id = f"ecmwf_native_{args.run}_{args.source_revision[:12]}"
    coverage_relative = Path("coverages") / "ecmwf" / coverage_id
    coverage_root = root / coverage_relative
    if coverage_root.exists():
        raise ValueError(f"ECMWF native coverage already exists: {coverage_id}")
    files, bytes_total = directory_stats(staging)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    deterministic_runs = [item[0] for item in deterministic_plan]
    deterministic_horizons = [item[1] for item in deterministic_plan]
    deterministic_roles = [
        "target"
        if source_run == args.run
        else "gust-support"
        if horizon == GUST_SUPPORT_MAX_FORECAST_HOUR
        else "previous-complete"
        if horizon == 360
        else "short-history"
        for source_run, horizon in deterministic_plan
    ]
    ensemble_runs = [item[0] for item in ensemble_plan]
    ensemble_horizons = [item[1] for item in ensemble_plan]
    grid = grid_contract()
    public_start = parse_run(args.run).strftime("%Y-%m-%dT%H:%M:%SZ")
    products = {
        MODEL: {
            "coverage_id": coverage_id,
            "runtime_domain": MODEL,
            "grid": grid,
        },
        ENSEMBLE_MODEL: {
            "coverage_id": coverage_id,
            "runtime_domain": ENSEMBLE_MODEL,
            "grid": grid,
            "source_runs": ensemble_runs,
            "source_run_max_forecast_hours": ensemble_horizons,
        },
    }
    marker: dict[str, Any] = {
        "version": 1,
        "status": "complete",
        "runtime_format": "openmeteo-native-v1",
        "group": "ecmwf",
        "coverage_id": coverage_id,
        "release_id": coverage_id,
        "coverage_path": coverage_relative.as_posix(),
        "latest_complete_run": args.run,
        "source_runs": deterministic_runs,
        "source_run_max_forecast_hours": deterministic_horizons,
        "source_run_roles": deterministic_roles,
        "short_run_count": SHORT_RUN_RETENTION,
        "full_run_count": COMPLETE_RUN_RETENTION,
        "gust_support_run_count": GUST_SUPPORT_RUN_RETENTION,
        "gust_support_max_forecast_hour": GUST_SUPPORT_MAX_FORECAST_HOUR,
        "public_start_utc": public_start,
        "products": products,
        "domain_grids": {MODEL: grid, ENSEMBLE_MODEL: grid},
        "producer_image": args.image,
        "openmeteo_upstream_commit": OPENMETEO_UPSTREAM_COMMIT,
        "regional_patch_sha256": args.patch_sha256,
        "source_revision": args.source_revision,
        "files": files,
        "bytes": bytes_total,
        "generated_at": generated_at,
    }
    atomic_write_json(staging / "coverage.json", marker)
    coverage_root.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, coverage_root)
    atomic_symlink(Path("..") / coverage_relative, root / "current" / "ecmwf")
    atomic_write_json(
        root / "groups" / "ecmwf" / "releases" / f"{coverage_id}.json",
        marker,
    )
    atomic_write_json(
        root / "groups" / "ecmwf" / "current" / "ready_for_processing.json",
        marker,
    )
    prune_coverages(root, args.keep_coverages, coverage_id)
    return marker


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--staging", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--patch-sha256", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--keep-coverages", type=int, default=2)
    args = parser.parse_args()
    try:
        payload = publish(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
