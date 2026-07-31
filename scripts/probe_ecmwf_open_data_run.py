#!/usr/bin/env python3
"""Read-only completeness probe for ECMWF Open Data forecast runs."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ecmwf_contract import (
    GUST_SUPPORT_MAX_FORECAST_HOUR,
    PRESSURE_LEVELS_HPA,
    PRESSURE_PROBE_PARAMS,
    SOIL_PROBE_FIELDS,
    parse_run,
    source_run_plan,
    surface_probe_params_for_horizon,
)

UTC = timezone.utc


def index_url(base_url: str, run: str, hour: int) -> str:
    date = run[:8]
    run_hour = run[8:]
    product = "oper"
    return (
        f"{base_url.rstrip('/')}/{date}/{run_hour}z/ifs/0p25/{product}/"
        f"{date}{run_hour}0000-{hour}h-{product}-fc.index"
    )


def load_index(url: str, timeout: float) -> list[dict[str, object]]:
    request = Request(url, headers={"User-Agent": "weather-forecast-server-ecmwf-probe/1"})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f"unexpected HTTP status {response.status}")
        body = response.read()
    records = []
    for line in body.splitlines():
        if line.strip():
            records.append(json.loads(line))
    if not records:
        raise ValueError("ECMWF index is empty")
    return records


def validate(
    run: str,
    records: list[dict[str, object]],
    max_forecast_hour: int = 360,
) -> dict[str, object]:
    expected_date = run[:8]
    expected_time = f"{run[8:]}00"
    expected_step = str(max_forecast_hour)
    mismatched = [
        record
        for record in records
        if str(record.get("date")) != expected_date
        or str(record.get("time")).zfill(4) != expected_time
        or str(record.get("step")) != expected_step
    ]
    if mismatched:
        raise ValueError(
            "ECMWF final index identity does not match requested "
            f"run/f{max_forecast_hour}"
        )

    surface = {
        str(record.get("param"))
        for record in records
        if str(record.get("levtype")) != "pl"
    }
    pressure = {
        (str(record.get("param")), int(str(record.get("levelist"))))
        for record in records
        if str(record.get("levtype")) == "pl"
        and str(record.get("levelist", "")).isdigit()
    }
    soil = {
        (str(record.get("param")), int(str(record.get("levelist"))))
        for record in records
        if str(record.get("levtype")) == "sol"
        and str(record.get("levelist", "")).isdigit()
    }
    required_surface = surface_probe_params_for_horizon(max_forecast_hour)
    missing_surface = sorted(required_surface - surface)
    gust_support_only = max_forecast_hour == GUST_SUPPORT_MAX_FORECAST_HOUR
    missing_soil = [] if gust_support_only else sorted(SOIL_PROBE_FIELDS - soil)
    missing_pressure = [] if gust_support_only else sorted(
        (param, level)
        for param in PRESSURE_PROBE_PARAMS
        for level in PRESSURE_LEVELS_HPA
        if (param, level) not in pressure
    )
    if missing_surface or missing_soil or missing_pressure:
        raise ValueError(
            f"ECMWF f{max_forecast_hour} inventory is incomplete: "
            f"surface={missing_surface}, soil={missing_soil}, "
            f"pressure={missing_pressure[:20]}"
        )
    return {
        "status": "complete",
        "run": run,
        "max_forecast_hour": max_forecast_hour,
        "index_records": len(records),
        "required_surface_params": len(required_surface),
        "required_soil_fields": 0 if gust_support_only else len(SOIL_PROBE_FIELDS),
        "required_pressure_fields": 0 if gust_support_only else len(PRESSURE_PROBE_PARAMS)
        * len(PRESSURE_LEVELS_HPA),
    }


def read_local_state(root: Path) -> tuple[datetime | None, tuple[str, ...]]:
    marker = root / "groups" / "ecmwf" / "current" / "ready_for_processing.json"
    if not marker.is_file():
        return None, ()
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if payload.get("status") != "complete":
            return None, ()
        run = parse_run(str(payload.get("latest_complete_run") or ""))
        source_runs = tuple(str(value) for value in payload.get("source_runs", ()))
    except (OSError, ValueError, json.JSONDecodeError):
        return None, ()
    if run.hour not in (0, 12):
        return None, ()
    return run, source_runs


def read_local_latest(root: Path) -> datetime | None:
    return read_local_state(root)[0]


def floor_to_long_run(now: datetime) -> datetime:
    current = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return current.replace(hour=12 if current.hour >= 12 else 0)


def candidate_runs(
    now: datetime,
    local_latest: datetime | None,
    lookback_hours: int,
    local_source_runs: tuple[str, ...] = (),
) -> list[datetime]:
    if lookback_hours < 12 or lookback_hours % 12:
        raise ValueError("lookback_hours must be a positive multiple of twelve")
    first = floor_to_long_run(now)
    runs = [
        first - timedelta(hours=12 * offset)
        for offset in range((lookback_hours // 12) + 1)
    ]
    if local_latest is None:
        return runs
    candidates = [run for run in runs if run > local_latest]
    expected = tuple(
        run
        for run, _horizon in source_run_plan(
            local_latest.strftime("%Y%m%d%H")
        )
    )
    if local_source_runs != expected and local_latest in runs:
        candidates.append(local_latest)
    return sorted(set(candidates), reverse=True)


def probe_run(
    base_url: str,
    run: str,
    timeout: float,
    max_forecast_hour: int = 360,
) -> dict[str, object]:
    parse_run(run)
    if max_forecast_hour <= 0:
        raise ValueError("max_forecast_hour must be positive")
    url = index_url(base_url, run, max_forecast_hour)
    payload = validate(
        run,
        load_index(url, timeout),
        max_forecast_hour,
    )
    payload["index_url"] = url
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--run")
    target.add_argument("--latest-ready", action="store_true")
    parser.add_argument("--data-root", type=Path, default=Path("./data/ecmwf"))
    parser.add_argument("--lookback-hours", type=int, default=72)
    parser.add_argument(
        "--base-url",
        default="https://data.ecmwf.int/forecasts",
    )
    parser.add_argument("--max-forecast-hour", type=int, default=360)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.latest_ready:
        local_latest, local_source_runs = read_local_state(args.data_root)
        try:
            candidates = candidate_runs(
                datetime.now(UTC),
                local_latest,
                args.lookback_hours,
                local_source_runs,
            )
        except ValueError as exc:
            parser.error(str(exc))
        for candidate in candidates:
            run = candidate.strftime("%Y%m%d%H")
            try:
                payload = probe_run(
                    args.base_url,
                    run,
                    args.timeout,
                    args.max_forecast_hour,
                )
            except (
                ValueError,
                HTTPError,
                URLError,
                TimeoutError,
                json.JSONDecodeError,
            ) as exc:
                print(f"NOT_READY {run} {exc}", file=sys.stderr)
                continue
            print(f"READY {run} {payload['index_url']}")
            return 0
        local = local_latest.strftime("%Y%m%d%H") if local_latest else "none"
        print(f"NOT_READY local_latest={local}", file=sys.stderr)
        return 1
    try:
        payload = probe_run(
            args.base_url,
            args.run,
            args.timeout,
            args.max_forecast_hour,
        )
    except (ValueError, HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {"status": "incomplete", "run": args.run, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
