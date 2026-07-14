#!/usr/bin/env python3
"""Build and compare exact Shanghai/Singapore WebP release inventories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


SCOPE_CONTRACT = {
    "gfs": {"product": "gfs013_surface", "manifest": "gfs013_surface_data.json", "layers": 18},
    "cams": {"product": "cams_global", "manifest": "cams_global_data.json", "layers": 4},
}
EXPECTED_FRAMES = 121


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_release_root(output_root: Path, marker: dict[str, Any]) -> Path:
    releases = (output_root / "releases").resolve()
    raw = Path(str(marker.get("path") or ""))
    release = (raw if raw.is_absolute() else output_root / raw).resolve()
    if release.parent != releases or not release.is_dir():
        raise ValueError(f"current marker release is outside {releases}: {release}")
    return release


def normalized_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("generated_at", None)
    payload.pop("source_release_id", None)
    return payload


def inventory_scope(output_root: Path, scope: str, strict: bool = True) -> dict[str, Any]:
    contract = SCOPE_CONTRACT[scope]
    marker_path = output_root / "current" / f"{scope}.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("status") != "complete" or marker.get("scope") != scope:
        raise ValueError(f"current {scope} WebP marker is not complete")
    release = safe_release_root(output_root, marker)
    product = release / contract["product"]
    manifest_path = product / contract["manifest"]
    manifest = normalized_manifest(manifest_path)
    run = str(marker.get("run") or manifest.get("source_run") or "")
    if run != str(manifest.get("source_run") or ""):
        raise ValueError(f"{scope} marker and manifest run differ")
    files = {
        path.relative_to(product).as_posix(): sha256_file(path)
        for path in sorted(product.rglob("*.webp"))
        if path.is_file() and not path.is_symlink()
    }
    expected_count = contract["layers"] * EXPECTED_FRAMES
    if strict:
        if manifest.get("frame_count") != EXPECTED_FRAMES:
            raise ValueError(f"{scope} manifest must contain {EXPECTED_FRAMES} frames")
        if len(manifest.get("layers") or {}) != contract["layers"]:
            raise ValueError(f"{scope} manifest has the wrong layer count")
        if len(files) != expected_count:
            raise ValueError(f"{scope} WebP count {len(files)}, expected {expected_count}")
    return {
        "scope": scope,
        "run": run,
        "product": contract["product"],
        "manifest": manifest,
        "webp_count": len(files),
        "expected_webp_count": expected_count,
        "files": files,
    }


def build_inventory(output_root: Path, strict: bool = True) -> dict[str, Any]:
    scopes = {scope: inventory_scope(output_root, scope, strict) for scope in SCOPE_CONTRACT}
    return {
        "version": 1,
        "strict": strict,
        "total_webp_count": sum(item["webp_count"] for item in scopes.values()),
        "scopes": scopes,
    }


def compare_inventories(shanghai: dict[str, Any], singapore: dict[str, Any]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    for scope in SCOPE_CONTRACT:
        left = shanghai.get("scopes", {}).get(scope)
        right = singapore.get("scopes", {}).get(scope)
        if not isinstance(left, dict) or not isinstance(right, dict):
            mismatches.append({"scope": scope, "reason": "missing_scope"})
            continue
        if left.get("run") != right.get("run"):
            mismatches.append(
                {"scope": scope, "reason": "run_mismatch", "shanghai": left.get("run"), "singapore": right.get("run")}
            )
        if left.get("manifest") != right.get("manifest"):
            mismatches.append({"scope": scope, "reason": "manifest_mismatch"})
        left_files = left.get("files") or {}
        right_files = right.get("files") or {}
        for path in sorted(set(left_files) | set(right_files)):
            if left_files.get(path) != right_files.get(path):
                mismatches.append(
                    {
                        "scope": scope,
                        "reason": "webp_sha256_mismatch",
                        "path": path,
                        "shanghai": left_files.get(path),
                        "singapore": right_files.get(path),
                    }
                )
    return {
        "passed": not mismatches,
        "exact_webp_bytes": True,
        "shanghai_total_webp_count": shanghai.get("total_webp_count"),
        "singapore_total_webp_count": singapore.get("total_webp_count"),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:100],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--output-root", required=True)
    inventory.add_argument("--output", required=True)
    inventory.add_argument("--allow-reduced-test", action="store_true")
    compare = subparsers.add_parser("compare")
    compare.add_argument("--shanghai-inventory", required=True)
    compare.add_argument("--singapore-inventory", required=True)
    compare.add_argument("--output-report", required=True)
    args = parser.parse_args()

    try:
        if args.command == "inventory":
            payload = build_inventory(Path(args.output_root), not args.allow_reduced_test)
            atomic_json(Path(args.output), payload)
            print(json.dumps({"status": "complete", "total_webp_count": payload["total_webp_count"]}))
            return 0
        shanghai = json.loads(Path(args.shanghai_inventory).read_text(encoding="utf-8"))
        singapore = json.loads(Path(args.singapore_inventory).read_text(encoding="utf-8"))
        report = compare_inventories(shanghai, singapore)
        atomic_json(Path(args.output_report), report)
        print(json.dumps({key: report[key] for key in ("passed", "mismatch_count")}))
        return 0 if report["passed"] else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
