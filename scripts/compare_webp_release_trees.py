#!/usr/bin/env python3
"""Compare every WebP byte in two product release directories."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def inventory(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): digest(path)
        for path in sorted(root.rglob("*.webp"))
        if path.is_file() and not path.is_symlink()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    left = inventory(args.left)
    right = inventory(args.right)
    mismatches = [path for path in sorted(set(left) | set(right)) if left.get(path) != right.get(path)]
    result = {
        "passed": not mismatches,
        "left_count": len(left),
        "right_count": len(right),
        "mismatch_count": len(mismatches),
        "mismatch_layers": dict(Counter(path.split("/", 1)[0] for path in mismatches)),
        "mismatches": mismatches[:100],
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.tmp.{os.getpid()}")
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
