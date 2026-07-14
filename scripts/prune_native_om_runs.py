#!/usr/bin/env python3
"""Prune unretained data_run batches and transient raw download data."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import sys


RUN_LEAF = re.compile(r"^[0-2][0-9][0-5][0-9]Z$")


def run_relative_path(run: str) -> Path:
    parsed = datetime.strptime(run, "%Y%m%d%H")
    return Path(parsed.strftime("%Y/%m/%d/%H00Z"))


def find_run_directories(domain_root: Path) -> list[Path]:
    found: list[Path] = []
    if not domain_root.exists():
        return found
    for candidate in domain_root.glob("*/*/*/*Z"):
        if not candidate.is_dir():
            continue
        relative = candidate.relative_to(domain_root)
        if len(relative.parts) != 4 or not RUN_LEAF.fullmatch(relative.parts[-1]):
            continue
        if not all(part.isdigit() for part in relative.parts[:3]):
            continue
        found.append(candidate)
    return found


def remove_empty_date_parents(domain_root: Path) -> None:
    for depth_pattern in ("*/*/*", "*/*", "*"):
        for path in sorted(domain_root.glob(depth_pattern), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()


def prune_native_runs(
    data_dir: Path,
    domains: list[str],
    retained_runs: list[str],
) -> dict[str, object]:
    data_dir = data_dir.resolve(strict=True)
    if not retained_runs:
        raise ValueError("retained_runs must not be empty")
    retained_paths = {run_relative_path(run) for run in retained_runs}
    removed_runs: dict[str, list[str]] = {}

    data_run_root = data_dir / "data_run"
    for domain in domains:
        if not domain or "/" in domain or "\\" in domain or domain in (".", ".."):
            raise ValueError(f"unsafe domain: {domain}")
        domain_root = data_run_root / domain
        missing = [relative for relative in retained_paths if not (domain_root / relative).is_dir()]
        if missing:
            missing_text = ",".join(path.as_posix() for path in sorted(missing))
            raise ValueError(f"{domain} is missing retained run directories: {missing_text}")
        removed: list[str] = []
        for run_dir in find_run_directories(domain_root):
            relative = run_dir.relative_to(domain_root)
            if relative in retained_paths:
                continue
            resolved = run_dir.resolve(strict=True)
            if resolved.parent.parent.parent.parent != domain_root.resolve(strict=True):
                raise ValueError(f"refusing to prune unscoped run directory: {resolved}")
            shutil.rmtree(resolved)
            removed.append(relative.as_posix())
        remove_empty_date_parents(domain_root)
        removed_runs[domain] = sorted(removed)

    removed_transient: list[str] = []
    for child in data_dir.iterdir():
        if not child.is_dir() or not (child.name.startswith("download-") or child.name == "http_cache"):
            continue
        resolved = child.resolve(strict=True)
        if resolved.parent != data_dir:
            raise ValueError(f"refusing to prune transient directory outside data root: {resolved}")
        shutil.rmtree(resolved)
        removed_transient.append(child.name)

    return {
        "retained_runs": retained_runs,
        "removed_runs": removed_runs,
        "removed_transient": sorted(removed_transient),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--domains", required=True)
    parser.add_argument("--retained-runs", required=True)
    args = parser.parse_args()
    try:
        result = prune_native_runs(
            Path(args.data_dir),
            [item.strip() for item in args.domains.split(",") if item.strip()],
            [item.strip() for item in args.retained_runs.split(",") if item.strip()],
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
