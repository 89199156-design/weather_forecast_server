#!/usr/bin/env python3
"""Install the immutable Open-Meteo ECMWF IFS 0.25 surface-height grid."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
from urllib.request import urlopen


ASSET_URL = (
    "https://openmeteo.s3.amazonaws.com/"
    "data/ecmwf_ifs025/static/HSURF.om"
)
ASSET_BYTES = 433_648
ASSET_SHA256 = (
    "935d56ba000b438b61504fbc271bfaa8f"
    "70db2acb541d58d5b466a24d294a9fb"
)
RELATIVE_PATH = Path("ecmwf_ifs025") / "HSURF.om"


def digest(path: Path) -> tuple[int, str]:
    sha256 = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            size += len(block)
            sha256.update(block)
    return size, sha256.hexdigest()


def verified(path: Path) -> bool:
    return path.is_file() and digest(path) == (ASSET_BYTES, ASSET_SHA256)


def ensure(root: Path, *, timeout: int = 60) -> dict[str, object]:
    target = root / RELATIVE_PATH
    if verified(target):
        status = "reused"
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.download.{os.getpid()}")
        temporary.unlink(missing_ok=True)
        try:
            with urlopen(ASSET_URL, timeout=timeout) as response:
                with temporary.open("wb") as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
            actual = digest(temporary)
            if actual != (ASSET_BYTES, ASSET_SHA256):
                raise ValueError(
                    "ECMWF HSURF verification failed: "
                    f"expected {ASSET_BYTES}/{ASSET_SHA256}, "
                    f"got {actual[0]}/{actual[1]}"
                )
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        status = "downloaded"
    return {
        "status": status,
        "path": str(target),
        "source_url": ASSET_URL,
        "bytes": ASSET_BYTES,
        "sha256": ASSET_SHA256,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    try:
        record = ensure(args.root.resolve(), timeout=args.timeout)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        " ".join(f"{key}={value}" for key, value in record.items()),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
