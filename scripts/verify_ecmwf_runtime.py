#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from ecmwf_contract import OPENMETEO_UPSTREAM_COMMIT, RAW_VARIABLES


def docker_labels(image: str) -> dict[str, str]:
    output = subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{json .Config.Labels}}"],
        text=True,
    )
    payload = json.loads(output)
    return {} if payload is None else {str(k): str(v) for k, v in payload.items()}


def verify(root: Path, image: str, patch: Path, source_revision: str) -> dict[str, object]:
    current = root / "current"
    if not current.is_symlink():
        raise ValueError("ECMWF current is not an atomic release symlink")
    resolved = current.resolve(strict=True)
    releases = (root / "releases").resolve(strict=True)
    if resolved.parent != releases:
        raise ValueError("ECMWF current resolves outside its release root")
    marker_path = root / "groups/ecmwf/current/ready_for_processing.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    release_marker = json.loads(
        (resolved / "ready_for_processing.json").read_text(encoding="utf-8")
    )
    if marker != release_marker:
        raise ValueError("ECMWF group and immutable release markers differ")
    if (
        marker.get("status") != "complete"
        or marker.get("latest_max_forecast_hour") != 360
        or marker.get("missing_required_variables")
        or marker.get("missing_optional_variables")
        or marker.get("required_variables") != list(RAW_VARIABLES)
    ):
        raise ValueError("ECMWF current marker is incomplete")
    if marker.get("source_revision") != source_revision:
        raise ValueError("ECMWF data was not produced by the deployed source revision")

    patch_sha256 = hashlib.sha256(patch.read_bytes()).hexdigest()
    labels = docker_labels(image)
    expected = {
        "io.weather-forecast.component": "ecmwf-native-engine",
        "io.weather-forecast.openmeteo-upstream-commit": OPENMETEO_UPSTREAM_COMMIT,
        "io.weather-forecast.ecmwf-patch-sha256": patch_sha256,
    }
    mismatches = {
        key: {"expected": value, "actual": labels.get(key)}
        for key, value in expected.items()
        if labels.get(key) != value
    }
    if mismatches:
        raise ValueError(f"ECMWF image provenance mismatch: {mismatches}")
    if marker.get("producer_image") != image:
        raise ValueError("ECMWF marker and runtime image differ")
    return {
        "status": "ready",
        "coverage_id": marker["coverage_id"],
        "latest_complete_run": marker["latest_complete_run"],
        "image": image,
        "source_revision": source_revision,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"[a-f0-9]{40}", args.source_revision):
            raise ValueError("source revision must be a full lowercase Git commit")
        payload = verify(
            Path(args.root).resolve(),
            args.image,
            Path(args.patch).resolve(),
            args.source_revision,
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(str(exc), file=os.sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
