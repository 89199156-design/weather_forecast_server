#!/usr/bin/env python3
"""Validate a published native Open-Meteo GFS coverage through a shadow API."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path, PurePosixPath
import sys
from typing import Any
import urllib.parse
import urllib.request

from gfs_schedule import gfs_forecast_hours
from publish_native_om_coverage import (
    GFS013_SKIP_HOUR_ZERO,
    GFS025_SKIP_HOUR_ZERO,
    validate_run_metadata,
)


UTC = timezone.utc
DEFAULT_POINTS = ((31.2304, 121.4737), (30.0, 110.0), (43.8256, 87.6168))
DEFAULT_VARIABLES = (
    "temperature_2m",
    "visibility",
    "cape",
    "pressure_msl",
    "uv_index_clear_sky",
    "temperature_850hPa",
)
GFS_SKIP_HOUR_ZERO = frozenset(GFS013_SKIP_HOUR_ZERO | GFS025_SKIP_HOUR_ZERO)


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    if parsed.minute or parsed.second or parsed.microsecond:
        raise ValueError(f"time is not aligned to an hour: {value}")
    return parsed


def parse_compact_run(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d%H").replace(tzinfo=UTC)


def iso_hour(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:00")


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def resolve_scoped_coverage(producer_root: Path, relative_value: Any) -> Path:
    if not isinstance(relative_value, str) or not relative_value:
        raise ValueError("ready marker has no coverage_path")
    relative = PurePosixPath(relative_value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe coverage_path: {relative_value}")
    expected_prefix = ("coverages", "gfs")
    if relative.parts[:2] != expected_prefix or len(relative.parts) != 3:
        raise ValueError(f"unexpected GFS coverage_path: {relative_value}")
    coverage = (producer_root / Path(*relative.parts)).resolve(strict=True)
    expected_parent = (producer_root / "coverages" / "gfs").resolve(strict=True)
    if coverage.parent != expected_parent:
        raise ValueError(f"coverage resolves outside GFS coverage root: {coverage}")
    return coverage


def validate_coverage_contract(producer_root: Path, min_public_hours: int = 300) -> dict[str, Any]:
    producer_root = producer_root.resolve(strict=True)
    marker_path = producer_root / "groups" / "gfs" / "current" / "ready_for_processing.json"
    if not marker_path.is_file():
        raise ValueError(f"missing ready marker: {marker_path}")
    marker = load_json(marker_path)
    coverage = resolve_scoped_coverage(producer_root, marker.get("coverage_path"))
    manifest_path = coverage / "coverage.json"
    if not manifest_path.is_file():
        raise ValueError(f"missing coverage manifest: {manifest_path}")
    manifest = load_json(manifest_path)

    required_identity = (
        "status",
        "runtime_format",
        "group",
        "coverage_id",
        "latest_complete_run",
        "source_runs",
        "historical_max_forecast_hour",
        "latest_max_forecast_hour",
        "short_run_count",
        "full_run_count",
        "source_run_max_forecast_hours",
        "public_start_utc",
        "local_day_start_utc",
        "public_end_utc",
        "public_hours",
        "domain_grids",
        "static_sources",
    )
    for field in required_identity:
        if marker.get(field) != manifest.get(field):
            raise ValueError(f"marker/manifest mismatch for {field}")
    if marker.get("status") != "complete":
        raise ValueError("coverage status is not complete")
    if marker.get("runtime_format") != "openmeteo-native-v1":
        raise ValueError("coverage is not native Open-Meteo runtime data")
    if marker.get("group") != "gfs":
        raise ValueError("coverage group is not gfs")
    domain_grids = marker.get("domain_grids")
    if not isinstance(domain_grids, dict):
        raise ValueError("coverage has no domain_grids contract")
    for domain in ("ncep_gfs013", "ncep_gfs025"):
        grid = domain_grids.get(domain)
        if not isinstance(grid, dict) or not all(
            isinstance(grid.get(field), (int, float))
            for field in (
                "nx",
                "ny",
                "lat_min",
                "lon_min",
                "dx",
                "dy",
                "dt_seconds",
                "om_file_length",
            )
        ):
            raise ValueError(f"coverage has an incomplete grid contract for {domain}")

    static_sources = marker.get("static_sources")
    dem = static_sources.get("copernicus_dem90") if isinstance(static_sources, dict) else None
    if not isinstance(dem, dict) or dem.get("source") != "copernicus_dem90":
        raise ValueError("coverage has no Copernicus DEM90 source contract")
    if dem.get("runtime_path") != "copernicus_dem90/static":
        raise ValueError("coverage has an unsafe Copernicus DEM90 runtime path")
    dem_lat_min = dem.get("latitude_chunk_min")
    dem_lat_max = dem.get("latitude_chunk_max")
    if (
        isinstance(dem_lat_min, bool)
        or isinstance(dem_lat_max, bool)
        or not isinstance(dem_lat_min, int)
        or not isinstance(dem_lat_max, int)
        or dem_lat_min > dem_lat_max
    ):
        raise ValueError("coverage has an invalid Copernicus DEM90 latitude range")
    expected_dem_files = dem_lat_max - dem_lat_min + 1
    if dem.get("file_count") != expected_dem_files:
        raise ValueError("Copernicus DEM90 file_count does not match its latitude range")
    dem_root = coverage / "copernicus_dem90" / "static"
    for latitude in range(dem_lat_min, dem_lat_max + 1):
        path = dem_root / f"lat_{latitude}.om"
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"missing Copernicus DEM90 latitude chunk: {latitude}")

    current = producer_root / "current" / "gfs"
    if not current.is_symlink():
        raise ValueError(f"current GFS pointer is not a symlink: {current}")
    if current.resolve(strict=True) != coverage:
        raise ValueError("current GFS pointer does not select the ready coverage")

    source_runs = marker.get("source_runs")
    if not isinstance(source_runs, list) or len(source_runs) != 5:
        raise ValueError("GFS coverage must contain exactly five source runs")
    parsed_runs = [parse_compact_run(str(run)) for run in source_runs]
    if any(right - left != timedelta(hours=6) for left, right in zip(parsed_runs, parsed_runs[1:])):
        raise ValueError("GFS source runs are not consecutive 6-hour cycles")
    oldest = parsed_runs[0]
    latest = parsed_runs[-1]
    if str(source_runs[-1]) != marker.get("latest_complete_run"):
        raise ValueError("latest source run does not match latest_complete_run")

    public_start = parse_utc(str(marker.get("public_start_utc")))
    public_end = parse_utc(str(marker.get("public_end_utc")))
    public_hours = int((public_end - public_start).total_seconds() // 3600)
    if marker.get("public_hours") != public_hours:
        raise ValueError("public_hours does not match public start/end")
    if public_hours < min_public_hours:
        raise ValueError(f"public GFS window is only {public_hours}h")
    if not oldest <= public_start <= latest:
        raise ValueError("public start is not covered by the five-run history window")
    if public_start != oldest:
        raise ValueError("public start is not the oldest retained GFS run")
    local_day_start = parse_utc(str(marker.get("local_day_start_utc")))
    if not public_start <= local_day_start <= latest:
        raise ValueError("UTC+8 local-day start is outside the retained history window")

    latest_max = manifest.get("latest_max_forecast_hour")
    if not isinstance(latest_max, int) or latest_max != 384:
        raise ValueError(f"latest GFS horizon must be 384h, got {latest_max}")
    historical_max = manifest.get("historical_max_forecast_hour")
    if not isinstance(historical_max, int) or historical_max != 5:
        raise ValueError(f"historical GFS horizon must be 5h, got {historical_max}")
    if public_end != latest + timedelta(hours=latest_max):
        raise ValueError("public end is not latest GFS run + 384h")
    if manifest.get("short_run_count") != 3:
        raise ValueError("GFS coverage must contain exactly three short source runs")
    if manifest.get("full_run_count") != 2:
        raise ValueError("GFS coverage must contain exactly two complete source runs")
    source_run_max_forecast_hours = manifest.get("source_run_max_forecast_hours")
    if source_run_max_forecast_hours != [5, 5, 5, 384, 384]:
        raise ValueError(
            "GFS source-run horizons must be three 0...5h runs followed by two 0...384h runs"
        )

    expected_domains = ("ncep_gfs013", "ncep_gfs025")
    for domain in expected_domains:
        if not (coverage / domain).is_dir():
            raise ValueError(f"missing runtime domain: {domain}")
        latest_path = coverage / "data_run" / domain / "latest.json"
        latest_metadata = load_json(latest_path)
        expected_reference = latest.strftime("%Y-%m-%dT%H:00:00Z")
        if latest_metadata.get("reference_time") != expected_reference:
            raise ValueError(f"{domain} latest metadata has the wrong reference_time")
        for source_run, max_forecast_hour in zip(
            source_runs, source_run_max_forecast_hours
        ):
            validate_run_metadata(
                coverage,
                domain,
                source_run,
                gfs_forecast_hours(max_forecast_hour),
            )

    return {
        "producer_root": str(producer_root),
        "coverage_path": str(coverage),
        "coverage_id": marker["coverage_id"],
        "source_runs": source_runs,
        "source_run_max_forecast_hours": source_run_max_forecast_hours,
        "latest": latest,
        "public_start": public_start,
        "local_day_start": local_day_start,
        "public_end": public_end,
        "public_hours": public_hours,
        "domain_grids": domain_grids,
        "static_sources": static_sources,
    }


def critical_hours(contract: dict[str, Any]) -> list[tuple[str, datetime]]:
    latest: datetime = contract["latest"]
    candidates = [
        ("retained_window_start", contract["public_start"]),
        ("local_day_start", contract["local_day_start"]),
        ("latest_run", latest),
        ("gfs_hour_120", latest + timedelta(hours=120)),
        ("interpolated_hour_121", latest + timedelta(hours=121)),
        ("interpolated_hour_122", latest + timedelta(hours=122)),
        ("gfs_hour_123", latest + timedelta(hours=123)),
        ("gfs_hour_384", latest + timedelta(hours=384)),
    ]
    seen: set[datetime] = set()
    return [(label, hour) for label, hour in candidates if not (hour in seen or seen.add(hour))]


def fetch_api_json(url: str, timeout: float) -> Any:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def value_is_finite(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def normalize_hour(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return iso_hour(parse_utc(value))
    except ValueError:
        return None


def validate_api_payload(
    payload: Any,
    *,
    points: list[tuple[float, float]],
    variables: list[str],
    hours: list[tuple[str, datetime]],
    source_run_references: set[datetime] | None = None,
) -> dict[str, Any]:
    responses = payload if isinstance(payload, list) else [payload]
    failures: list[dict[str, Any]] = []
    source_run_reference_hours = {
        iso_hour(reference) for reference in (source_run_references or set())
    }
    if len(responses) != len(points):
        failures.append(
            {"reason": "point_count_mismatch", "expected": len(points), "actual": len(responses)}
        )
        return {"passed": False, "failures": failures, "critical_hours": {}}

    indexes: list[dict[str, int]] = []
    hourlies: list[dict[str, Any]] = []
    for point_index, response in enumerate(responses):
        hourly = response.get("hourly") if isinstance(response, dict) else None
        if not isinstance(hourly, dict):
            failures.append({"reason": "missing_hourly", "point_index": point_index})
            hourly = {}
        hourlies.append(hourly)
        time_index: dict[str, int] = {}
        for index, value in enumerate(hourly.get("time") or []):
            normalized = normalize_hour(value)
            if normalized is not None:
                time_index[normalized] = index
        indexes.append(time_index)

    evidence: dict[str, Any] = {}
    for label, hour in hours:
        requested = iso_hour(hour)
        evidence[label] = {"time": requested, "variables": {}}
        for point_index, time_index in enumerate(indexes):
            if requested not in time_index:
                failures.append(
                    {"reason": "missing_critical_time", "point_index": point_index, "time": requested}
                )
        for variable in variables:
            finite_points: list[int] = []
            represented_points: list[int] = []
            values: list[Any] = []
            for point_index, hourly in enumerate(hourlies):
                index = indexes[point_index].get(requested)
                series = hourly.get(variable)
                represented = (
                    isinstance(series, list)
                    and index is not None
                    and index < len(series)
                )
                value = series[index] if represented else None
                values.append(value)
                if represented:
                    represented_points.append(point_index)
                if value_is_finite(value):
                    finite_points.append(point_index)
            expected_missing = (
                not finite_points
                and bool(points)
                and len(represented_points) == len(points)
                and all(value is None for value in values)
                and requested in source_run_reference_hours
                and variable in GFS_SKIP_HOUR_ZERO
            )
            evidence[label]["variables"][variable] = {
                "finite_points": finite_points,
                "represented_points": represented_points,
                "values": values,
                "expected_missing": expected_missing,
            }
            if expected_missing:
                evidence[label]["variables"][variable]["expected_missing_reason"] = (
                    "official_gfs_skip_hour_zero_at_source_run_reference"
                )
            elif not finite_points:
                failures.append(
                    {
                        "reason": "all_null_or_non_finite",
                        "time": requested,
                        "variable": variable,
                    }
                )
    return {"passed": not failures, "failures": failures, "critical_hours": evidence}


def parse_point(value: str) -> tuple[float, float]:
    try:
        latitude_text, longitude_text = value.split(",", 1)
        latitude, longitude = float(latitude_text), float(longitude_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("point must be LATITUDE,LONGITUDE") from exc
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise argparse.ArgumentTypeError("point is outside latitude/longitude bounds")
    return latitude, longitude


def build_api_url(
    base_url: str,
    points: list[tuple[float, float]],
    variables: list[str],
    start: datetime,
    end: datetime,
) -> str:
    params = {
        "latitude": ",".join(str(point[0]) for point in points),
        "longitude": ",".join(str(point[1]) for point in points),
        "hourly": ",".join(variables),
        "timezone": "UTC",
        "timeformat": "iso8601",
        "start_hour": iso_hour(start),
        "end_hour": iso_hour(end),
        "models": "gfs_global",
        "wind_speed_unit": "ms",
    }
    return base_url.rstrip("/") + "/v1/forecast?" + urllib.parse.urlencode(params)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-root", required=True)
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--point", action="append", type=parse_point)
    parser.add_argument("--variables", default=",".join(DEFAULT_VARIABLES))
    parser.add_argument("--min-public-hours", type=int, default=300)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output-report", required=True)
    args = parser.parse_args()

    report: dict[str, Any] = {"passed": False, "failures": []}
    try:
        contract = validate_coverage_contract(Path(args.producer_root), args.min_public_hours)
        points = list(args.point or DEFAULT_POINTS)
        variables = [item.strip() for item in args.variables.split(",") if item.strip()]
        if not variables:
            raise ValueError("at least one variable is required")
        hours = critical_hours(contract)
        url = build_api_url(
            args.api_base_url,
            points,
            variables,
            contract["public_start"],
            contract["public_end"],
        )
        api_result = validate_api_payload(
            fetch_api_json(url, args.timeout),
            points=points,
            variables=variables,
            hours=hours,
            source_run_references={
                parse_compact_run(str(run)) for run in contract["source_runs"]
            },
        )
        report = {
            "passed": api_result["passed"],
            "coverage_id": contract["coverage_id"],
            "coverage_path": contract["coverage_path"],
            "source_runs": contract["source_runs"],
            "public_start_utc": iso_hour(contract["public_start"]),
            "public_end_utc": iso_hour(contract["public_end"]),
            "public_hours": contract["public_hours"],
            "points": [{"latitude": lat, "longitude": lon} for lat, lon in points],
            "variables": variables,
            "failures": api_result["failures"],
            "critical_hours": api_result["critical_hours"],
        }
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        report["failures"] = [{"reason": "validation_error", "error": str(exc)}]

    output = Path(args.output_report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "failures": len(report["failures"]), "report": str(output)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
