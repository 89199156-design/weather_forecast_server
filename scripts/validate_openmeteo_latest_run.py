#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gfs_schedule import gfs_forecast_hours


UTC = timezone.utc


def compact_to_iso(compact: str) -> str:
    run = datetime.strptime(compact, "%Y%m%d%H").replace(tzinfo=UTC)
    return run.strftime("%Y-%m-%dT%H:00:00Z")


def compact_to_datetime(compact: str) -> datetime:
    return datetime.strptime(compact, "%Y%m%d%H").replace(tzinfo=UTC)


def parse_valid_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


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
    parser.add_argument(
        "--gfs-max-forecast-hour",
        type=int,
        default=None,
        help="Require the official hourly/3-hourly GFS schedule through this horizon",
    )
    parser.add_argument("--required-gfs-pressure-domain", default=None)
    parser.add_argument("--required-gfs-pressure-levels", default=None)
    parser.add_argument("--required-gfs-pressure-variables", default=None)
    args = parser.parse_args()

    if args.min_frames < 0:
        parser.error("--min-frames must not be negative")
    if args.gfs_max_forecast_hour is not None:
        try:
            gfs_forecast_hours(args.gfs_max_forecast_hour)
        except ValueError as exc:
            parser.error(str(exc))

    expected_reference = compact_to_iso(args.run)
    run_datetime = compact_to_datetime(args.run)
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
        if args.gfs_max_forecast_hour is not None:
            try:
                actual_times = {parse_valid_time(value) for value in valid_times}
            except (TypeError, ValueError) as exc:
                failures.append(f"{domain}: invalid valid_times: {exc}")
            else:
                expected_times = [
                    run_datetime + timedelta(hours=hour)
                    for hour in gfs_forecast_hours(args.gfs_max_forecast_hour)
                ]
                missing_times = [value for value in expected_times if value not in actual_times]
                if missing_times:
                    failures.append(
                        f"{domain}: missing {len(missing_times)} official GFS valid_times; "
                        f"first_missing={missing_times[0].strftime('%Y-%m-%dT%H:%M:%SZ')}"
                    )
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
