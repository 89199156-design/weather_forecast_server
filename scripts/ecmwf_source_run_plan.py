#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from ecmwf_contract import source_run_plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--format", choices=("json", "lines"), default="json")
    args = parser.parse_args()
    plan = source_run_plan(args.run)
    previous_complete_run = next(
        run for run, horizon in reversed(plan[:-1]) if horizon == 360
    )
    payload = [
        {
            "run": run,
            "max_forecast_hour": horizon,
            "role": (
                "target"
                if run == args.run
                else "previous-complete"
                if run == previous_complete_run
                else "short-history"
            ),
        }
        for run, horizon in plan
    ]
    if args.format == "lines":
        for item in payload:
            print(
                f"{item['run']}|{item['max_forecast_hour']}|{item['role']}"
            )
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
