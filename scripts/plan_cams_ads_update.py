#!/usr/bin/env python3
"""Plan one ADS task exclusively from complete local CAMS source coverages."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

from validate_native_cams_coverage import validate_cams_contract
from validate_native_cams_greenhouse_coverage import validate_greenhouse_contract


UTC = timezone.utc
ADS_WORK_PATTERN = re.compile(r"cams_ads_(\d{10})")
ADS_JOB_PATTERN = re.compile(r"(\d{10})\.json")


def find_persisted_request(producer_root: Path) -> tuple[str, str, str] | None:
    """Return the one resumable ADS request without consulting newer ECPDS data.

    A submitted CDS job belongs to its original fixed target directory.  It
    must be completed (or reach an explicit terminal state) before a newer
    local ECPDS date can cause another POST.
    """
    root = producer_root.resolve() / "ads_staging"
    if not root.exists():
        return None
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"unsafe ADS staging root: {root}")
    found: list[tuple[str, str, str]] = []
    for work_dir in sorted(root.iterdir()):
        match = ADS_WORK_PATTERN.fullmatch(work_dir.name)
        if match is None:
            continue
        if not work_dir.is_dir() or work_dir.is_symlink():
            raise ValueError(f"unsafe ADS work directory: {work_dir}")
        target_run = match.group(1)
        target = datetime.strptime(target_run, "%Y%m%d%H").replace(tzinfo=UTC)
        if target.hour != 0:
            raise ValueError(f"ADS target must use 00 UTC: {target_run}")
        source_runs = [
            (target - timedelta(days=offset)).strftime("%Y%m%d00")
            for offset in range(2, -1, -1)
        ]
        jobs_dir = work_dir / ".ads_jobs"
        if not jobs_dir.exists():
            continue
        if not jobs_dir.is_dir() or jobs_dir.is_symlink():
            raise ValueError(f"unsafe ADS job-state directory: {jobs_dir}")
        for state_path in sorted(jobs_dir.iterdir()):
            state_match = ADS_JOB_PATTERN.fullmatch(state_path.name)
            if state_match is None:
                raise ValueError(f"unexpected ADS job-state entry: {state_path}")
            if not state_path.is_file() or state_path.is_symlink():
                raise ValueError(f"unsafe ADS job-state file: {state_path}")
            source_run = state_match.group(1)
            if source_run not in source_runs:
                raise ValueError(
                    f"ADS job state {source_run} is outside target window {target_run}"
                )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError(f"invalid ADS job state: {state_path}")
            job = state.get("job")
            phase = str(state.get("phase") or "")
            has_job_id = isinstance(job, dict) and bool(str(job.get("jobID") or ""))
            if (
                not isinstance(state.get("dataset"), str)
                or not state.get("dataset")
                or not isinstance(state.get("server"), str)
                or not state.get("server")
                or not isinstance(state.get("requestBody"), str)
                or not state.get("requestBody")
                or (not has_job_id and phase != "submitting")
            ):
                raise ValueError(f"invalid ADS job state: {state_path}")
            found.append((target_run, ",".join(source_runs), source_run))
    if len(found) > 1:
        raise ValueError("multiple persisted ADS requests require manual reconciliation")
    return found[0] if found else None


def plan_update(producer_root: Path, force_current: bool = False) -> str:
    persisted = find_persisted_request(producer_root)
    if persisted is not None:
        target_run, source_runs, pending_run = persisted
        return f"RESUME {target_run} {source_runs} {pending_run}"
    try:
        main = validate_cams_contract(producer_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"SKIP local_ecpds_not_complete {exc}"
    main_run = str(main["source_runs"][-1])
    parsed_main = datetime.strptime(main_run, "%Y%m%d%H").replace(tzinfo=UTC)
    target = parsed_main.replace(hour=0)
    target_run = target.strftime("%Y%m%d00")

    greenhouse_marker = (
        producer_root
        / "groups"
        / "cams_greenhouse"
        / "current"
        / "ready_for_processing.json"
    )
    greenhouse = None
    if greenhouse_marker.exists() or greenhouse_marker.is_symlink():
        # A genuinely missing marker is the normal first-publication state.
        # Once a marker exists, validation errors must stop the task instead of
        # being misclassified as absence and hidden by a replacement publish.
        greenhouse = validate_greenhouse_contract(producer_root)
    if greenhouse is not None:
        greenhouse_latest = str(greenhouse["latest_complete_run"])
        if greenhouse_latest == target_run and not force_current:
            return f"SKIP ads_already_complete {target_run}"
        if greenhouse_latest > target_run:
            return "ERROR local_ads_run_is_newer_than_ecpds_date"

    source_runs = [
        (target - timedelta(days=offset)).strftime("%Y%m%d00")
        for offset in range(2, -1, -1)
    ]
    return f"READY {main_run} {target_run} {','.join(source_runs)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-root", required=True)
    parser.add_argument(
        "--force-current",
        action="store_true",
        help="rebuild and republish the current target run through the normal immutable pipeline",
    )
    args = parser.parse_args()
    try:
        result = plan_update(Path(args.producer_root), force_current=args.force_current)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR {exc}")
        return 2
    print(result)
    return 2 if result.startswith("ERROR ") else 0


if __name__ == "__main__":
    raise SystemExit(main())
