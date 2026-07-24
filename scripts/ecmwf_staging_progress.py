#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def load(path: Path, target_run: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "target_run": target_run, "completed_runs": []}
    if (
        payload.get("version") != 1
        or payload.get("target_run") != target_run
        or not isinstance(payload.get("completed_runs"), list)
    ):
        raise ValueError("incompatible ECMWF staging progress")
    return payload


def atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--target-run", required=True)
    parser.add_argument("--mark-run")
    parser.add_argument("--is-complete")
    args = parser.parse_args()
    path = Path(args.path)
    payload = load(path, args.target_run)
    completed = [str(value) for value in payload["completed_runs"]]
    if args.mark_run:
        if args.mark_run not in completed:
            completed.append(args.mark_run)
            payload["completed_runs"] = completed
            atomic_write(path, payload)
    if args.is_complete:
        return 0 if args.is_complete in completed else 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
