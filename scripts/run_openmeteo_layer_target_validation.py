#!/usr/bin/env python3
"""Run targeted WebP layer parity batches against an Open-Meteo API."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import validate_openmeteo_layers as layer_validator  # noqa: E402


def point_key(case: dict[str, Any]) -> tuple[int, int]:
    return int(case["y"]), int(case["x"])


def build_layer_point_batches(
    *,
    grid: dict[str, Any],
    batches: int,
    points_per_batch: int,
    point_offset: float,
) -> list[list[dict[str, Any]]]:
    if batches <= 0:
        raise ValueError("batches must be positive")
    if points_per_batch <= 0:
        raise ValueError("points_per_batch must be positive")
    if not 0.0 <= point_offset < 1.0:
        raise ValueError("point_offset must be in [0, 1)")
    total_points = batches * points_per_batch
    width = int(grid["grid_width"])
    height = int(grid["grid_height"])
    total_cells = width * height
    if total_points > total_cells:
        raise ValueError("requested validation points exceed layer grid cells")
    indices: list[int] = []
    for index in range(total_points):
        fraction = (index + 0.5 + point_offset) / total_points
        flat = int(math.floor(fraction * total_cells))
        indices.append(min(total_cells - 1, flat))
    if len(set(indices)) != len(indices):
        raise ValueError("generated layer validation points are not unique")
    cases: list[dict[str, Any]] = []
    for flat in indices:
        y = flat // width
        x = flat - y * width
        lat, lon = layer_validator.grid_center(grid, y=y, x=x)
        cases.append({"flat": flat, "y": y, "x": x, "lat": lat, "lon": lon})
    return [cases[index : index + points_per_batch] for index in range(0, len(cases), points_per_batch)]


def stems_by_time(manifest: dict[str, Any]) -> dict[str, str]:
    return layer_validator.stems_by_time(manifest)


def validate_layer_batch(
    *,
    batch_index: int,
    cases: list[dict[str, Any]],
    manifest: dict[str, Any],
    layer_dir: Path,
    layers: dict[str, Any],
    api_base_url: str,
    api_host_header: str | None,
    reference_ssh_host: str | None,
    frames: int,
    timeout_seconds: float,
    request_retries: int,
    request_retry_delay: float,
    request_pause: float,
    api_response: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected_times = list(manifest.get("times") or [])[:frames]
    if len(selected_times) != frames:
        raise ValueError(f"manifest has {len(selected_times)} frames, expected {frames}")
    stem_by_time = stems_by_time(manifest)
    variables = layer_validator.variables_from_manifest(layers)
    response = api_response
    if response is None:
        response = layer_validator.fetch_api_chunk_for_manifest(
            manifest=manifest,
            api_base_url=api_base_url,
            latitudes=[case["lat"] for case in cases],
            longitudes=[case["lon"] for case in cases],
            variables=variables,
            timeout_seconds=timeout_seconds,
            api_host_header=api_host_header,
            reference_ssh_host=reference_ssh_host,
            request_retries=request_retries,
            request_retry_delay=request_retry_delay,
            request_pause=request_pause,
        )
    if len(response) != len(cases):
        raise ValueError(f"API point count mismatch: got {len(response)} expected {len(cases)}")

    checked = 0
    mismatches: list[dict[str, Any]] = []
    for case, api_item in zip(cases, response):
        hourly = api_item.get("hourly") or {}
        api_times = hourly.get("time") or []
        for valid_time in selected_times:
            try:
                api_time_index = api_times.index(valid_time)
            except ValueError as exc:
                raise ValueError(f"API response missing time {valid_time}") from exc
            stem = stem_by_time[valid_time]
            for layer_name, layer in layers.items():
                if layer.get("data_type") == "vector":
                    api_variables = layer["api_variables"]
                    expected_u = layer_validator.transform_api_value(hourly[api_variables[0]][api_time_index], layer)
                    expected_v = layer_validator.transform_api_value(hourly[api_variables[1]][api_time_index], layer)
                    actual_u, actual_v = layer_validator.decode_layer_value(
                        layer_dir,
                        layer_name,
                        layer,
                        stem,
                        int(case["y"]),
                        int(case["x"]),
                    )
                    checked += 2
                    if not layer_validator.values_match(expected_u, actual_u, scale=10.0) or not layer_validator.values_match(
                        expected_v,
                        actual_v,
                        scale=10.0,
                    ):
                        mismatches.append(
                            {
                                "point": case,
                                "time": valid_time,
                                "layer": layer_name,
                                "expected_u": expected_u,
                                "actual_u": actual_u,
                                "expected_v": expected_v,
                                "actual_v": actual_v,
                            }
                        )
                else:
                    api_variable = layer["api_variables"][0]
                    expected = layer_validator.transform_api_value(hourly[api_variable][api_time_index], layer)
                    actual = layer_validator.decode_layer_value(
                        layer_dir,
                        layer_name,
                        layer,
                        stem,
                        int(case["y"]),
                        int(case["x"]),
                    )
                    checked += 1
                    if not layer_validator.values_match(expected, actual, scale=float(layer["scale"])):
                        mismatches.append(
                            {
                                "point": case,
                                "time": valid_time,
                                "layer": layer_name,
                                "api_variable": api_variable,
                                "expected": expected,
                                "actual": actual,
                                "scale": layer["scale"],
                            }
                        )
    return {
        "batch": batch_index,
        "passed": not mismatches,
        "points": cases,
        "frames": frames,
        "layers": list(layers.keys()),
        "checked_values": checked,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:50],
    }


def summarize_batch_results(
    batch_results: list[dict[str, Any]],
    *,
    planned_batches: int,
    points_per_batch: int,
    frames: int,
    failure_limit: int,
    layers: list[str],
) -> dict[str, Any]:
    failed_batches = sum(1 for result in batch_results if not result["passed"])
    stopped_reason = None
    if failed_batches >= failure_limit:
        stopped_reason = "failure_limit_reached"
    elif len(batch_results) < planned_batches:
        stopped_reason = "stopped_before_all_batches"
    return {
        "passed": len(batch_results) == planned_batches and failed_batches == 0,
        "planned_batches": planned_batches,
        "completed_batches": len(batch_results),
        "points_per_batch": points_per_batch,
        "planned_points": planned_batches * points_per_batch,
        "completed_points": len(batch_results) * points_per_batch,
        "frames": frames,
        "failed_batches": failed_batches,
        "failure_limit": failure_limit,
        "stopped_reason": stopped_reason,
        "layers": layers,
        "checked_values": sum(int(result["checked_values"]) for result in batch_results),
        "batch_results": batch_results,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run targeted Open-Meteo WebP layer parity batches.")
    parser.add_argument("--layer-dir", required=True)
    parser.add_argument("--manifest-name")
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--api-host-header")
    parser.add_argument("--reference-ssh-host")
    parser.add_argument("--output-dir", default="docs/validation/reports/layer-target")
    parser.add_argument("--layers")
    parser.add_argument("--batches", type=int, default=100)
    parser.add_argument("--points-per-batch", type=int, default=10)
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--failure-limit", type=int, default=3)
    parser.add_argument("--point-offset", type=float, default=0.0)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--request-retries", type=int, default=3)
    parser.add_argument("--request-retry-delay", type=float, default=2.0)
    parser.add_argument("--request-pause", type=float, default=0.0)
    parser.add_argument("--reference-points-per-request", type=int, default=100)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.failure_limit <= 0:
        raise ValueError("--failure-limit must be positive")
    if args.reference_points_per_request < args.points_per_batch:
        raise ValueError("--reference-points-per-request must be >= --points-per-batch")
    layer_dir = Path(args.layer_dir)
    manifest_path = layer_validator.manifest_path_for_layer_dir(layer_dir, args.manifest_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    layers = layer_validator.selected_layer_items(manifest["layers"], args.layers)
    point_batches = build_layer_point_batches(
        grid=manifest["grid"],
        batches=args.batches,
        points_per_batch=args.points_per_batch,
        point_offset=args.point_offset,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    batch_results: list[dict[str, Any]] = []
    failures = 0
    started = time.time()
    batches_per_reference = max(1, args.reference_points_per_request // args.points_per_batch)
    variables = layer_validator.variables_from_manifest(layers)
    for batch_group_start in range(0, len(point_batches), batches_per_reference):
        batch_group = point_batches[batch_group_start : batch_group_start + batches_per_reference]
        grouped_cases = [case for cases in batch_group for case in cases]
        grouped_response = layer_validator.fetch_api_chunk_for_manifest(
            manifest=manifest,
            api_base_url=args.api_base_url,
            latitudes=[case["lat"] for case in grouped_cases],
            longitudes=[case["lon"] for case in grouped_cases],
            variables=variables,
            timeout_seconds=args.timeout_seconds,
            api_host_header=args.api_host_header,
            reference_ssh_host=args.reference_ssh_host,
            request_retries=args.request_retries,
            request_retry_delay=args.request_retry_delay,
            request_pause=args.request_pause,
        )
        if len(grouped_response) != len(grouped_cases):
            raise ValueError(f"API point count mismatch: got {len(grouped_response)} expected {len(grouped_cases)}")
        for group_offset, cases in enumerate(batch_group):
            batch_index = batch_group_start + group_offset + 1
            response_start = group_offset * args.points_per_batch
            response_end = response_start + len(cases)
            result = validate_layer_batch(
                batch_index=batch_index,
                cases=cases,
                manifest=manifest,
                layer_dir=layer_dir,
                layers=layers,
                api_base_url=args.api_base_url,
                api_host_header=args.api_host_header,
                reference_ssh_host=args.reference_ssh_host,
                frames=args.frames,
                timeout_seconds=args.timeout_seconds,
                request_retries=args.request_retries,
                request_retry_delay=args.request_retry_delay,
                request_pause=args.request_pause,
                api_response=grouped_response[response_start:response_end],
            )
            report_path = output_dir / f"batch-{batch_index:02d}.json"
            write_json(report_path, result)
            batch_results.append(
                {
                    "batch": batch_index,
                    "passed": result["passed"],
                    "points": cases,
                    "checked_values": result["checked_values"],
                    "mismatch_count": result["mismatch_count"],
                    "report": str(report_path),
                }
            )
            if not result["passed"]:
                failures += 1
            summary = summarize_batch_results(
                batch_results,
                planned_batches=args.batches,
                points_per_batch=args.points_per_batch,
                frames=args.frames,
                failure_limit=args.failure_limit,
                layers=list(layers.keys()),
            )
            summary.update(
                {
                    "manifest": str(manifest_path),
                    "layer_dir": str(layer_dir),
                    "scope": manifest.get("scope", "gfs"),
                    "api_base_url": args.api_base_url,
                    "api_host_header": args.api_host_header,
                    "reference_ssh_host": args.reference_ssh_host,
                    "reference_points_per_request": args.reference_points_per_request,
                    "elapsed_seconds": round(time.time() - started, 3),
                }
            )
            write_json(output_dir / "summary.progress.json", summary)
            print(json.dumps({"batch": batch_index, "passed": result["passed"], "failed_batches": failures}), flush=True)
            if failures >= args.failure_limit:
                break
        if failures >= args.failure_limit:
            break

    summary = summarize_batch_results(
        batch_results,
        planned_batches=args.batches,
        points_per_batch=args.points_per_batch,
        frames=args.frames,
        failure_limit=args.failure_limit,
        layers=list(layers.keys()),
    )
    summary.update(
        {
            "manifest": str(manifest_path),
            "layer_dir": str(layer_dir),
            "scope": manifest.get("scope", "gfs"),
            "api_base_url": args.api_base_url,
            "api_host_header": args.api_host_header,
            "reference_ssh_host": args.reference_ssh_host,
            "reference_points_per_request": args.reference_points_per_request,
            "elapsed_seconds": round(time.time() - started, 3),
        }
    )
    summary_path = output_dir / f"summary-{args.batches}x{args.points_per_batch}x{args.frames}.json"
    write_json(summary_path, summary)
    print(json.dumps({"passed": summary["passed"], "summary": str(summary_path)}, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
