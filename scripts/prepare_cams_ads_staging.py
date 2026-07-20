#!/usr/bin/env python3
"""Prepare or resume private CAMS ADS input without locking other producers."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from seed_native_om_staging import coverage_data_stats, safe_current_coverage


def prepare_ads_staging(producer_root: Path, staging: Path) -> dict[str, object]:
    producer_root = producer_root.resolve()
    expected_parent = (producer_root / "ads_staging").resolve()
    if staging.resolve().parent != expected_parent:
        raise ValueError(f"ADS staging must be directly under {expected_parent}")
    if staging.exists():
        if not staging.is_dir() or staging.is_symlink():
            raise ValueError(f"unsafe ADS staging path: {staging}")
        return {"resumed": True, "seeded_from": None}

    staging.mkdir(parents=True)
    current = safe_current_coverage(producer_root, "cams_greenhouse")
    # One-time migration compatibility: older production coverages stored ADS
    # beside ECPDS in the ``cams`` group. Reuse only the greenhouse subtree so
    # the first split ADS update remains a missing-run fill instead of
    # needlessly submitting an already complete day again. Once the independent
    # namespace exists it always wins and the legacy coverage is never read.
    if current is None:
        # `cams` is owned by the concurrent ECPDS task. ADS may read it for the
        # one-time split migration but must never repair or move its pointer.
        current = safe_current_coverage(
            producer_root,
            "cams",
            repair_pointer=False,
        )
    if current is None:
        return {"resumed": False, "seeded_from": None}
    coverage, marker = current
    products = marker.get("products")
    if (
        marker.get("group") not in {"cams_greenhouse", "cams"}
        or not isinstance(products, dict)
        or "cams_global_greenhouse_gases" not in products
    ):
        return {"resumed": False, "seeded_from": None}
    for relative, copy_function in (
        # Runtime OM files are mutable while a missing ADS day is imported.
        # Copy them so a downloader can never modify the hard-linked immutable
        # current coverage. Per-run source metadata is never modified and can
        # safely remain hard-linked until a specific missing run is replaced.
        (Path("cams_global_greenhouse_gases"), shutil.copy2),
        (Path("data_run") / "cams_global_greenhouse_gases", os.link),
    ):
        source = coverage / relative
        if not source.is_dir():
            raise ValueError(f"published ADS source is missing: {source}")
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, copy_function=copy_function, symlinks=True)
    return {"resumed": False, "seeded_from": str(coverage)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-root", required=True)
    parser.add_argument("--staging-dir", required=True)
    args = parser.parse_args()
    try:
        result = prepare_ads_staging(
            Path(args.producer_root),
            Path(args.staging_dir),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
