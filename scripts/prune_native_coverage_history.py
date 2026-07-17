#!/usr/bin/env python3
"""Remove non-current native OM coverages after API snapshot confirmation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from typing import Any


COVERAGE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def validate_coverage_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not COVERAGE_ID.fullmatch(value):
        raise ValueError(f"invalid {field}: {value!r}")
    return value


def expected_coverage_relative(scope: str, coverage_id: str) -> PurePosixPath:
    return PurePosixPath("coverages", scope, coverage_id)


def validate_current_coverage(
    producer_root: Path,
    scope: str,
    expected_coverage_id: str,
) -> tuple[Path, Path, dict[str, Any]]:
    marker_path = (
        producer_root
        / "groups"
        / scope
        / "current"
        / "ready_for_processing.json"
    )
    marker = load_json(marker_path)
    if marker.get("status") != "complete":
        raise ValueError(f"current {scope} marker is not complete")
    if marker.get("runtime_format") != "openmeteo-native-v1":
        raise ValueError(f"current {scope} marker is not native OM")
    if marker.get("group") != scope:
        raise ValueError(f"current marker group does not match scope {scope}")
    coverage_id = validate_coverage_id(marker.get("coverage_id"), "coverage_id")
    if coverage_id != expected_coverage_id:
        raise ValueError(
            f"current coverage changed before pruning: {coverage_id} != "
            f"{expected_coverage_id}"
        )

    relative_value = marker.get("coverage_path")
    if not isinstance(relative_value, str):
        raise ValueError("current marker coverage_path is missing")
    relative = PurePosixPath(relative_value)
    expected_relative = expected_coverage_relative(scope, coverage_id)
    if relative.is_absolute() or ".." in relative.parts or relative != expected_relative:
        raise ValueError(f"unsafe current coverage_path: {relative_value}")

    coverages_root = (producer_root / "coverages" / scope).resolve(strict=True)
    coverage_candidate = producer_root / Path(*relative.parts)
    if coverage_candidate.is_symlink() or not coverage_candidate.is_dir():
        raise ValueError(f"current coverage is not a real directory: {coverage_candidate}")
    coverage_root = coverage_candidate.resolve(strict=True)
    if coverage_root.parent != coverages_root:
        raise ValueError(f"current coverage escapes scoped root: {coverage_root}")

    current_link = producer_root / "current" / scope
    if not current_link.is_symlink():
        raise ValueError(f"current {scope} pointer is not a symlink: {current_link}")
    if current_link.resolve(strict=True) != coverage_root:
        raise ValueError("current coverage pointer and ready marker do not match")

    manifest = load_json(coverage_root / "coverage.json")
    if (
        manifest.get("status") != "complete"
        or manifest.get("runtime_format") != "openmeteo-native-v1"
        or manifest.get("group") != scope
        or manifest.get("coverage_id") != coverage_id
    ):
        raise ValueError(f"current coverage manifest identity is invalid: {coverage_root}")
    return coverages_root, coverage_root, marker


def validate_old_coverage(
    candidate: Path,
    coverages_root: Path,
    scope: str,
) -> Path:
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError(f"unexpected entry in coverage root: {candidate}")
    coverage_id = validate_coverage_id(candidate.name, "coverage directory name")
    resolved = candidate.resolve(strict=True)
    if resolved.parent != coverages_root:
        raise ValueError(f"coverage escapes scoped root: {resolved}")
    manifest = load_json(resolved / "coverage.json")
    if (
        manifest.get("status") != "complete"
        or manifest.get("runtime_format") != "openmeteo-native-v1"
        or manifest.get("group") != scope
        or manifest.get("coverage_id") != coverage_id
    ):
        raise ValueError(f"old coverage manifest identity is invalid: {resolved}")
    return resolved


def validate_old_release_marker(
    path: Path,
    releases_root: Path,
    scope: str,
    current_coverage_id: str,
) -> Path | None:
    if path.is_symlink() or not path.is_file() or path.suffix != ".json":
        raise ValueError(f"unexpected entry in release marker root: {path}")
    resolved = path.resolve(strict=True)
    if resolved.parent != releases_root:
        raise ValueError(f"release marker escapes scoped root: {resolved}")
    coverage_id = validate_coverage_id(path.stem, "release marker name")
    marker = load_json(resolved)
    marker_coverage_id = validate_coverage_id(
        marker.get("coverage_id"), "release marker coverage_id"
    )
    if coverage_id != marker_coverage_id:
        raise ValueError(f"release marker filename does not match identity: {resolved}")
    if (
        marker.get("status") != "complete"
        or marker.get("runtime_format") != "openmeteo-native-v1"
        or marker.get("group") != scope
        or marker.get("release_id") != coverage_id
    ):
        raise ValueError(f"release marker identity is invalid: {resolved}")
    relative_value = marker.get("coverage_path")
    if not isinstance(relative_value, str):
        raise ValueError(f"release marker coverage_path is missing: {resolved}")
    relative = PurePosixPath(relative_value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative != expected_coverage_relative(scope, coverage_id)
    ):
        raise ValueError(f"unsafe release marker coverage_path: {relative_value}")
    return None if coverage_id == current_coverage_id else resolved


def prune_coverage_history(
    producer_root: Path,
    scope: str,
    expected_coverage_id: str,
) -> dict[str, object]:
    if scope not in {"gfs", "cams"}:
        raise ValueError(f"unsupported scope: {scope}")
    expected_coverage_id = validate_coverage_id(
        expected_coverage_id, "expected_coverage_id"
    )
    producer_root = producer_root.resolve(strict=True)
    coverages_root, current_coverage, _ = validate_current_coverage(
        producer_root, scope, expected_coverage_id
    )

    old_coverages: list[Path] = []
    for candidate in coverages_root.iterdir():
        if candidate == current_coverage:
            continue
        old_coverages.append(validate_old_coverage(candidate, coverages_root, scope))

    releases_root = producer_root / "groups" / scope / "releases"
    old_release_markers: list[Path] = []
    if releases_root.exists():
        if releases_root.is_symlink() or not releases_root.is_dir():
            raise ValueError(f"release marker root is not a directory: {releases_root}")
        releases_root = releases_root.resolve(strict=True)
        for path in releases_root.iterdir():
            old = validate_old_release_marker(
                path, releases_root, scope, expected_coverage_id
            )
            if old is not None:
                old_release_markers.append(old)

    # Re-read every current pointer after validating the deletion set. If a
    # publisher changed identity, leave all history in place for the next run.
    _, confirmed_current, _ = validate_current_coverage(
        producer_root, scope, expected_coverage_id
    )
    if confirmed_current != current_coverage:
        raise ValueError("current coverage changed while preparing prune")

    for old_coverage in old_coverages:
        shutil.rmtree(old_coverage)
    for old_marker in old_release_markers:
        old_marker.unlink()

    return {
        "scope": scope,
        "current_coverage_id": expected_coverage_id,
        "removed_coverages": sorted(path.name for path in old_coverages),
        "removed_release_markers": sorted(path.name for path in old_release_markers),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-root", required=True)
    parser.add_argument("--scope", choices=("gfs", "cams"), required=True)
    parser.add_argument("--expected-coverage-id", required=True)
    args = parser.parse_args()
    try:
        result = prune_coverage_history(
            Path(args.producer_root), args.scope, args.expected_coverage_id
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
