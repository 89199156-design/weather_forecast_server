#!/usr/bin/env python3
"""Read-only completeness probe for one ECMWF Open Data deterministic run."""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ecmwf_contract import (
    PRESSURE_LEVELS_HPA,
    PRESSURE_PROBE_PARAMS,
    SOIL_PROBE_FIELDS,
    SURFACE_PROBE_PARAMS,
    parse_run,
)


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


def validate(run: str, records: list[dict[str, object]]) -> dict[str, object]:
    expected_date = run[:8]
    expected_time = f"{run[8:]}00"
    mismatched = [
        record
        for record in records
        if str(record.get("date")) != expected_date
        or str(record.get("time")).zfill(4) != expected_time
        or str(record.get("step")) != "360"
    ]
    if mismatched:
        raise ValueError("ECMWF final index identity does not match requested run/f360")

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
    missing_surface = sorted(SURFACE_PROBE_PARAMS - surface)
    missing_soil = sorted(SOIL_PROBE_FIELDS - soil)
    missing_pressure = sorted(
        (param, level)
        for param in PRESSURE_PROBE_PARAMS
        for level in PRESSURE_LEVELS_HPA
        if (param, level) not in pressure
    )
    if missing_surface or missing_soil or missing_pressure:
        raise ValueError(
            "ECMWF f360 inventory is incomplete: "
            f"surface={missing_surface}, soil={missing_soil}, "
            f"pressure={missing_pressure[:20]}"
        )
    return {
        "status": "complete",
        "run": run,
        "max_forecast_hour": 360,
        "index_records": len(records),
        "required_surface_params": len(SURFACE_PROBE_PARAMS),
        "required_soil_fields": len(SOIL_PROBE_FIELDS),
        "required_pressure_fields": len(PRESSURE_PROBE_PARAMS)
        * len(PRESSURE_LEVELS_HPA),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument(
        "--base-url",
        default="https://data.ecmwf.int/forecasts",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    try:
        run = parse_run(args.run)
        if run.hour != 0:
            raise ValueError("probe target must be a 00Z long run")
        url = index_url(args.base_url, args.run, 360)
        payload = validate(args.run, load_index(url, args.timeout))
        payload["index_url"] = url
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
