#!/usr/bin/env python3
"""Plan consecutive source runs retained inside one native OM coverage."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json


UTC = timezone.utc


@dataclass(frozen=True)
class SourceRunPlan:
    latest_run: str
    source_runs: tuple[str, ...]
    historical_max_forecast_hour: int
    latest_max_forecast_hour: int
    local_day_start_utc: str
    public_start_utc: str
    public_end_utc: str
    public_hours: int


def parse_run(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d%H").replace(tzinfo=UTC)


def compact(value: datetime) -> str:
    return value.strftime("%Y%m%d%H")


def iso_z(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def local_midnight_utc(latest: datetime, utc_offset_hours: int) -> datetime:
    local = latest.astimezone(timezone(timedelta(hours=utc_offset_hours)))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def plan_source_runs(
    latest_run: str,
    *,
    cadence_hours: int,
    source_run_count: int,
    historical_max_forecast_hour: int,
    latest_max_forecast_hour: int,
    local_utc_offset_hours: int,
) -> SourceRunPlan:
    latest = parse_run(latest_run)
    if cadence_hours <= 0 or 24 % cadence_hours:
        raise ValueError("cadence_hours must be a positive divisor of 24")
    if latest.hour % cadence_hours:
        raise ValueError(f"run is not aligned to a {cadence_hours}h cycle")
    if source_run_count < 1:
        raise ValueError("source_run_count must be positive")
    if historical_max_forecast_hour < cadence_hours - 1:
        raise ValueError("historical horizon must bridge at least one run cadence")
    if latest_max_forecast_hour < 0:
        raise ValueError("latest horizon must not be negative")

    runs = tuple(
        compact(latest - timedelta(hours=cadence_hours * offset))
        for offset in reversed(range(source_run_count))
    )
    oldest = parse_run(runs[0])
    local_day_start = local_midnight_utc(latest, local_utc_offset_hours)
    if local_day_start < oldest:
        raise ValueError(
            f"{source_run_count} runs cover only back to {iso_z(oldest)}, "
            f"before local midnight {iso_z(local_day_start)}"
        )
    public_start = oldest
    public_end = latest + timedelta(hours=latest_max_forecast_hour)
    return SourceRunPlan(
        latest_run=latest_run,
        source_runs=runs,
        historical_max_forecast_hour=historical_max_forecast_hour,
        latest_max_forecast_hour=latest_max_forecast_hour,
        local_day_start_utc=iso_z(local_day_start),
        public_start_utc=iso_z(public_start),
        public_end_utc=iso_z(public_end),
        public_hours=int((public_end - public_start).total_seconds() // 3600),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--cadence-hours", type=int, required=True)
    parser.add_argument("--source-run-count", type=int, required=True)
    parser.add_argument("--historical-max-forecast-hour", type=int, required=True)
    parser.add_argument("--latest-max-forecast-hour", type=int, required=True)
    parser.add_argument("--local-utc-offset-hours", type=int, default=8)
    parser.add_argument("--format", choices=("json", "fields", "imports"), default="json")
    args = parser.parse_args()

    try:
        plan = plan_source_runs(
            args.run,
            cadence_hours=args.cadence_hours,
            source_run_count=args.source_run_count,
            historical_max_forecast_hour=args.historical_max_forecast_hour,
            latest_max_forecast_hour=args.latest_max_forecast_hour,
            local_utc_offset_hours=args.local_utc_offset_hours,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.format == "fields":
        print(
            ",".join(plan.source_runs),
            plan.public_start_utc,
            plan.public_end_utc,
            plan.public_hours,
            plan.local_day_start_utc,
        )
    elif args.format == "imports":
        for run in plan.source_runs[:-1]:
            print(run, plan.historical_max_forecast_hour)
        print(plan.source_runs[-1], plan.latest_max_forecast_hour)
    else:
        print(json.dumps(asdict(plan), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
