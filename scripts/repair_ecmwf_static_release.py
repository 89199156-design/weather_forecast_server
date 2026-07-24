#!/usr/bin/env python3
"""Publish a revisioned ECMWF release with a corrected immutable HSURF grid."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

from ecmwf_contract import MODEL, OPENMETEO_UPSTREAM_COMMIT
from ensure_ecmwf_static_asset import (
    ASSET_BYTES,
    ASSET_SHA256,
    ASSET_URL,
)
from publish_ecmwf_release import (
    atomic_symlink,
    atomic_write_json,
    directory_stats,
    validate_release,
)


def sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def repair(
    *,
    root: Path,
    run: str,
    regional_hsurf: Path,
    image: str,
    patch_sha256: str,
    source_revision: str,
) -> dict[str, object]:
    if not re.fullmatch(r"\d{8}00", run):
        raise ValueError("ECMWF repair run must be YYYYMMDD00")
    if not re.fullmatch(r"[a-f0-9]{64}", patch_sha256):
        raise ValueError("ECMWF patch SHA-256 is invalid")
    if not re.fullmatch(r"[a-f0-9]{40}", source_revision):
        raise ValueError("source revision must be a full lowercase Git commit")
    if not regional_hsurf.is_file():
        raise ValueError(f"regional HSURF is missing: {regional_hsurf}")

    current = root / "current"
    if not current.is_symlink():
        raise ValueError("ECMWF current release pointer is missing")
    source_release = current.resolve(strict=True)
    releases_root = (root / "releases").resolve()
    if source_release.parent != releases_root:
        raise ValueError("ECMWF current release escapes the managed release root")
    source_marker_path = source_release / "ready_for_processing.json"
    source_marker = json.loads(source_marker_path.read_text(encoding="utf-8"))
    if (
        source_marker.get("status") != "complete"
        or source_marker.get("latest_complete_run") != run
        or source_marker.get("latest_max_forecast_hour") != 360
        or source_marker.get("missing_required_variables")
        or source_marker.get("missing_optional_variables")
    ):
        raise ValueError("ECMWF source release is not a complete target batch")
    validate_release(source_release, run)

    coverage_id = f"{MODEL}_{run}_{source_revision[:12]}"
    coverage_root = root / "releases" / coverage_id
    if coverage_root.exists():
        raise ValueError(f"ECMWF immutable release already exists: {coverage_id}")
    staging = root / "staging" / f".static-repair-{coverage_id}-{os.getpid()}"
    if staging.exists():
        raise ValueError(f"ECMWF repair staging already exists: {staging}")

    try:
        shutil.copytree(source_release, staging, copy_function=os.link)
        marker_path = staging / "ready_for_processing.json"
        marker_path.unlink()
        target_hsurf = staging / MODEL / "static" / "HSURF.om"
        target_hsurf.unlink()
        shutil.copyfile(regional_hsurf, target_hsurf)
        regional_bytes, regional_sha256 = sha256_file(target_hsurf)
        if regional_bytes <= 0:
            raise ValueError("regional HSURF is empty")

        validate_release(staging, run)
        files, bytes_total = directory_stats(staging)
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        marker = dict(source_marker)
        marker.update(
            {
                "coverage_id": coverage_id,
                "release_id": coverage_id,
                "coverage_path": f"releases/{coverage_id}",
                "producer_image": image,
                "openmeteo_upstream_commit": OPENMETEO_UPSTREAM_COMMIT,
                "regional_patch_sha256": patch_sha256,
                "source_revision": source_revision,
                "files": files,
                "bytes": bytes_total,
                "generated_at": generated_at,
                "static_assets": {
                    "surface_height": {
                        "source_url": ASSET_URL,
                        "source_bytes": ASSET_BYTES,
                        "source_sha256": ASSET_SHA256,
                        "regional_bytes": regional_bytes,
                        "regional_sha256": regional_sha256,
                    }
                },
                "repair": {
                    "kind": "official-static-surface-height",
                    "source_release_id": source_marker["release_id"],
                    "published_at": generated_at,
                },
            }
        )
        atomic_write_json(marker_path, marker)
        os.replace(staging, coverage_root)
        atomic_symlink(Path("releases") / coverage_id, current)
        atomic_write_json(
            root / "groups" / "ecmwf" / "current" / "ready_for_processing.json",
            marker,
        )
        atomic_write_json(
            root / "groups" / "ecmwf" / "releases" / f"{coverage_id}.json",
            marker,
        )
        return marker
    except Exception:
        if staging.exists() and staging.parent == (root / "staging"):
            shutil.rmtree(staging)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--regional-hsurf", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--patch-sha256", required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    try:
        marker = repair(
            root=args.root.resolve(),
            run=args.run,
            regional_hsurf=args.regional_hsurf.resolve(),
            image=args.image,
            patch_sha256=args.patch_sha256,
            source_revision=args.source_revision,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(marker, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
