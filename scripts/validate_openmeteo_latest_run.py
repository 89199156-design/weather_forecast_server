#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


UTC = timezone.utc


def compact_to_iso(compact: str) -> str:
    run = datetime.strptime(compact, "%Y%m%d%H").replace(tzinfo=UTC)
    return run.strftime("%Y-%m-%dT%H:00:00Z")


def read_latest(data_dir: Path, domain: str) -> dict:
    path = data_dir / "data_run" / domain / "latest.json"
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def gfs_pressure_variable_dirs(variables: list[str], levels: list[str]) -> list[str]:
    return [f"{variable}_{level}hPa" for variable in variables for level in levels]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate latest Open-Meteo data_run metadata for a target run.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--run", required=True, help="Run in YYYYMMDDHH")
    parser.add_argument("--domains", required=True, help="Comma-separated data_run domains")
    parser.add_argument("--min-frames", type=int, default=121)
    parser.add_argument("--required-gfs-pressure-domain", default=None)
    parser.add_argument("--required-gfs-pressure-levels", default=None)
    parser.add_argument("--required-gfs-pressure-variables", default=None)
    args = parser.parse_args()

    expected_reference = compact_to_iso(args.run)
    data_dir = Path(args.data_dir)
    failures: list[str] = []
    required_dirs_by_domain: dict[str, list[str]] = {}
    if args.required_gfs_pressure_domain:
        required_dirs_by_domain[args.required_gfs_pressure_domain] = gfs_pressure_variable_dirs(
            parse_csv(args.required_gfs_pressure_variables),
            parse_csv(args.required_gfs_pressure_levels),
        )

    for domain in [item.strip() for item in args.domains.split(",") if item.strip()]:
        try:
            payload = read_latest(data_dir, domain)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            failures.append(f"{domain}: {exc}")
            continue
        reference = payload.get("reference_time")
        valid_times = payload.get("valid_times") or []
        if reference != expected_reference:
            failures.append(f"{domain}: reference_time={reference}, expected={expected_reference}")
        if len(valid_times) < args.min_frames:
            failures.append(f"{domain}: frames={len(valid_times)}, expected_at_least={args.min_frames}")
        for variable_dir in required_dirs_by_domain.get(domain, []):
            if not (data_dir / domain / variable_dir).is_dir():
                failures.append(f"{domain}: missing required variable directory {variable_dir}")

    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1

    print(f"OK {expected_reference} {args.domains}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
