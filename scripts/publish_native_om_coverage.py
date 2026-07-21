#!/usr/bin/env python3
"""Atomically publish an immutable native Open-Meteo runtime coverage."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any

from gfs_schedule import gfs_forecast_hours, gfs_full_run_hours
from native_grid_contract import gfs_domain_grids
from om_v3_metadata import read_array_dimensions


UTC = timezone.utc
GFS_DOMAINS = ("ncep_gfs013", "ncep_gfs025")
DEFAULT_GFS013_REQUIRED = "temperature_2m,surface_temperature,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,relative_humidity_2m,precipitation,wind_v_component_10m,wind_u_component_10m,snow_depth,showers,snowfall_water_equivalent,uv_index,uv_index_clear_sky,boundary_layer_height,shortwave_radiation,latent_heat_flux,sensible_heat_flux,diffuse_radiation,total_column_integrated_water_vapour,soil_temperature_0_to_10cm,soil_temperature_10_to_40cm,soil_temperature_40_to_100cm,soil_temperature_100_to_200cm,soil_moisture_0_to_10cm,soil_moisture_10_to_40cm,soil_moisture_40_to_100cm,soil_moisture_100_to_200cm"
DEFAULT_GFS025_REQUIRED = "pressure_msl,categorical_freezing_rain,temperature_80m,temperature_100m,wind_v_component_80m,wind_u_component_80m,wind_v_component_100m,wind_u_component_100m,wind_gusts_10m,freezing_level_height,cape,lifted_index,convective_inhibition,visibility"
DEFAULT_PRESSURE_LEVELS = "1000,975,950,925,900,850,800,750,700,650,600,550,500,450,400,350,300,250,200,150,100,50"
DEFAULT_PRESSURE_VARIABLES = "temperature,wind_u_component,wind_v_component,geopotential_height,cloud_cover,relative_humidity,vertical_velocity"
DEFAULT_DEM_LAT_MIN = 0
DEFAULT_DEM_LAT_MAX = 58
GFS013_SKIP_HOUR_ZERO = {
    "categorical_freezing_rain",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "precipitation",
    "showers",
    "snowfall_water_equivalent",
    "sensible_heat_flux",
    "latent_heat_flux",
    "shortwave_radiation",
    "diffuse_radiation",
    "uv_index",
    "uv_index_clear_sky",
}
GFS025_SKIP_HOUR_ZERO = {
    "categorical_freezing_rain",
    "precipitation",
    "showers",
    "sensible_heat_flux",
    "latent_heat_flux",
    "shortwave_radiation",
    "diffuse_radiation",
    "uv_index",
    "uv_index_clear_sky",
}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_compact_run(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d%H").replace(tzinfo=UTC)


def parse_iso_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def parse_source_runs(value: str | list[str]) -> list[str]:
    runs = value if isinstance(value, list) else value.split(",")
    return [run.strip() for run in runs if run.strip()]


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def required_variables_by_domain(args: argparse.Namespace) -> dict[str, set[str]]:
    levels = parse_csv(getattr(args, "required_pressure_levels", DEFAULT_PRESSURE_LEVELS))
    families = parse_csv(getattr(args, "required_pressure_variables", DEFAULT_PRESSURE_VARIABLES))
    pressure = {f"{family}_{level}hPa" for family in families for level in levels}
    return {
        "ncep_gfs013": set(
            parse_csv(getattr(args, "required_gfs013_variables", DEFAULT_GFS013_REQUIRED))
        ),
        "ncep_gfs025": set(
            parse_csv(getattr(args, "required_gfs025_variables", DEFAULT_GFS025_REQUIRED))
        )
        | pressure,
    }


def require_variables(payload: dict[str, Any], required: set[str], domain: str, run: str) -> None:
    available = set(payload.get("variables") or [])
    missing = sorted(required - available)
    if missing:
        raise ValueError(
            f"{domain} run {run} is missing required variables: {','.join(missing[:20])}"
        )


def validate_dem_static(staging: Path, lat_min: int, lat_max: int) -> dict[str, Any]:
    if lat_min > lat_max or lat_min < -90 or lat_max > 89:
        raise ValueError("invalid Copernicus DEM90 latitude chunk range")
    static_root = staging / "copernicus_dem90" / "static"
    missing = [
        latitude
        for latitude in range(lat_min, lat_max + 1)
        if not (static_root / f"lat_{latitude}.om").is_file()
        or (static_root / f"lat_{latitude}.om").stat().st_size <= 0
    ]
    if missing:
        raise ValueError(
            "missing required Copernicus DEM90 latitude chunks: "
            + ",".join(str(value) for value in missing[:20])
        )
    return {
        "source": "copernicus_dem90",
        "runtime_path": "copernicus_dem90/static",
        "latitude_chunk_min": lat_min,
        "latitude_chunk_max": lat_max,
        "file_count": lat_max - lat_min + 1,
    }


def gfs_stored_frame_counts(domain: str, variables: list[str], schedule_count: int) -> dict[str, int]:
    skip_hour_zero = GFS013_SKIP_HOUR_ZERO if domain == "ncep_gfs013" else GFS025_SKIP_HOUR_ZERO
    return {
        variable: schedule_count - 1 if variable in skip_hour_zero else schedule_count
        for variable in variables
    }


def validate_gfs_window(args: argparse.Namespace) -> tuple[list[str], list[int]]:
    latest = parse_compact_run(args.latest_run)
    source_runs = parse_source_runs(args.source_runs)
    if len(source_runs) != 5:
        raise ValueError(f"GFS coverage must contain five source runs, got {len(source_runs)}")
    parsed_runs = [parse_compact_run(run) for run in source_runs]
    if any(run.hour not in (0, 6, 12, 18) for run in parsed_runs):
        raise ValueError("source runs must be 00/06/12/18 UTC GFS cycles")
    if parsed_runs[-1] != latest or source_runs[-1] != args.latest_run:
        raise ValueError("latest run must be the final source run")
    if any(right - left != timedelta(hours=6) for left, right in zip(parsed_runs, parsed_runs[1:])):
        raise ValueError("GFS source runs must be five consecutive 6-hour cycles")
    if args.full_run_count != 2:
        raise ValueError("GFS coverage must contain exactly two complete source runs")
    short_run_count = len(source_runs) - args.full_run_count
    if short_run_count != 3:
        raise ValueError("GFS coverage must contain exactly three short source runs")
    gfs_forecast_hours(args.latest_max_forecast_hour)
    if args.latest_max_forecast_hour != 384:
        raise ValueError("latest GFS run must contain the complete 0...384h horizon")
    gfs_forecast_hours(args.historical_max_forecast_hour)
    if args.historical_max_forecast_hour != 5:
        raise ValueError("historical GFS runs must contain forecast hours 0 through 5")

    public_start = parse_iso_time(args.public_start_utc)
    public_end = parse_iso_time(args.public_end_utc)
    expected_end = latest + timedelta(hours=args.latest_max_forecast_hour)
    if public_end != expected_end:
        raise ValueError(
            f"public_end_utc={args.public_end_utc}, expected={expected_end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )
    expected_hours = int((public_end - public_start).total_seconds() // 3600)
    if expected_hours != args.public_hours:
        raise ValueError(f"public_hours={args.public_hours}, expected={expected_hours}")
    if not parsed_runs[0] <= public_start <= latest:
        raise ValueError("public_start_utc must fall inside the five-run history window")
    if public_start != parsed_runs[0]:
        raise ValueError("public_start_utc must equal the oldest retained GFS run")
    local_day_start = parse_iso_time(args.local_day_start_utc)
    if not public_start <= local_day_start <= latest:
        raise ValueError("local_day_start_utc is outside the retained GFS history")
    source_run_max_forecast_hours = (
        [args.historical_max_forecast_hour] * short_run_count
        + [args.latest_max_forecast_hour] * args.full_run_count
    )
    return source_runs, source_run_max_forecast_hours


def read_latest(staging: Path, domain: str, expected_run: str) -> dict[str, Any]:
    path = staging / "data_run" / domain / "latest.json"
    if not path.is_file():
        raise ValueError(f"missing latest metadata: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_reference = parse_compact_run(expected_run).strftime("%Y-%m-%dT%H:00:00Z")
    if payload.get("reference_time") != expected_reference:
        raise ValueError(
            f"{domain} reference_time={payload.get('reference_time')}, expected={expected_reference}"
        )
    if not payload.get("valid_times"):
        raise ValueError(f"{domain} has no valid_times")
    if not (staging / domain).is_dir():
        raise ValueError(f"missing runtime domain: {staging / domain}")
    return payload


def run_directory(staging: Path, domain: str, run: str) -> Path:
    parsed = parse_compact_run(run)
    return staging / "data_run" / domain / parsed.strftime("%Y/%m/%d/%H00Z")


def validate_run_metadata(
    staging: Path,
    domain: str,
    run: str,
    expected_forecast_hours: list[int],
    expected_stored_time_count: int | dict[str, int] | None = None,
) -> dict[str, Any]:
    directory = run_directory(staging, domain, run)
    path = directory / "meta.json"
    if not path.is_file():
        raise ValueError(f"missing retained run metadata: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_reference = parse_compact_run(run)
    reference = parse_iso_time(str(payload.get("reference_time")))
    if reference != expected_reference:
        raise ValueError(f"{domain} run {run} has the wrong reference_time")
    actual_times = sorted({parse_iso_time(str(value)) for value in payload.get("valid_times") or []})
    expected_times = [expected_reference + timedelta(hours=hour) for hour in expected_forecast_hours]
    if actual_times != expected_times:
        raise ValueError(
            f"{domain} run {run} valid_times do not match forecast hours "
            f"{expected_forecast_hours[0]}...{expected_forecast_hours[-1]}"
        )
    variables = payload.get("variables")
    if not isinstance(variables, list) or not variables:
        raise ValueError(f"{domain} run {run} has no variables")
    missing_files = [variable for variable in variables if not (directory / f"{variable}.om").is_file()]
    if missing_files:
        raise ValueError(f"{domain} run {run} is missing variable files: {','.join(missing_files[:10])}")
    if expected_stored_time_count is not None:
        for variable in variables:
            file_path = directory / f"{variable}.om"
            dimensions = read_array_dimensions(file_path)
            expected_count = (
                expected_stored_time_count.get(variable)
                if isinstance(expected_stored_time_count, dict)
                else expected_stored_time_count
            )
            if expected_count is None:
                raise ValueError(f"{domain} run {run} has no stored-frame contract for {variable}")
            if len(dimensions) != 3 or dimensions[2] != expected_count:
                raise ValueError(
                    f"{domain} run {run} variable {variable} stored time count "
                    f"{dimensions[-1] if dimensions else 'none'}, expected {expected_count}"
                )
    return payload


def validate_runtime_variables(
    staging: Path,
    domain: str,
    variables: list[str] | set[str],
) -> None:
    """Require the latest runtime chunks without using directory-wide totals."""
    missing = []
    for variable in variables:
        variable_root = staging / domain / variable
        if not variable_root.is_dir() or not any(
            path.is_file() and not path.is_symlink() and path.stat().st_size > 0
            for path in variable_root.glob("*.om")
        ):
            missing.append(variable)
    if missing:
        raise ValueError(
            f"{domain} is missing runtime variables: {','.join(sorted(missing)[:20])}"
        )


def validate_gfs_retained_run(
    staging: Path,
    run: str,
    max_forecast_hour: int,
    required_by_domain: dict[str, set[str]],
) -> None:
    """Validate one retained GFS run with the same contract used at publish time."""
    expected_forecast_hours = gfs_full_run_hours(max_forecast_hour)
    for domain in GFS_DOMAINS:
        run_meta = validate_run_metadata(
            staging,
            domain,
            run,
            expected_forecast_hours,
        )
        require_variables(run_meta, required_by_domain[domain], domain, run)
        validate_run_metadata(
            staging,
            domain,
            run,
            expected_forecast_hours,
            gfs_stored_frame_counts(
                domain,
                run_meta["variables"],
                len(expected_forecast_hours),
            ),
        )


def directory_stats(root: Path) -> tuple[int, int]:
    files = 0
    bytes_total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            files += 1
            bytes_total += path.stat().st_size
    return files, bytes_total


def producer_image_ref() -> str | None:
    """Return the immutable Swift producer image selected for this coverage."""
    image = os.environ.get("WEATHER_OPENMETEO_IMAGE", "").strip()
    tag = os.environ.get("WEATHER_OPENMETEO_TAG", "").strip()
    if not image or not tag:
        return None
    return f"{image}:{tag}"


def ensure_staging_is_scoped(staging: Path, output_root: Path) -> None:
    staging_parent = staging.resolve().parent
    expected_parent = (output_root / "staging").resolve()
    if staging_parent != expected_parent:
        raise ValueError(f"staging directory must be directly under {expected_parent}")


def atomic_symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.tmp.{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, link)


def select_coverage_id_for_publish(
    output_root: Path,
    group: str,
    base_coverage_id: str,
    latest_run: str,
) -> str:
    """Choose a new immutable identity only for a repair of the current run.

    A coverage directory may already exist because the process was interrupted
    after moving staging but before publishing the current marker.  In that
    case the base identity must be reused so marker publication can resume.
    Once a complete marker already identifies the same run, however, a fresh
    staging directory is a physical repair and must never be discarded in
    favour of the previously published (possibly damaged) directory.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", base_coverage_id):
        raise ValueError("base coverage_id is unsafe")
    coverage_root = output_root / "coverages" / group / base_coverage_id
    if not coverage_root.exists():
        return base_coverage_id

    marker_path = (
        output_root
        / "groups"
        / group
        / "current"
        / "ready_for_processing.json"
    )
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return base_coverage_id
    if (
        marker.get("status") != "complete"
        or marker.get("group") != group
        or str(marker.get("latest_complete_run") or "") != latest_run
    ):
        return base_coverage_id

    stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%S%f")
    repair_base = f"{base_coverage_id}_repair_{stamp}_{os.getpid()}"
    candidate = repair_base
    sequence = 0
    while (output_root / "coverages" / group / candidate).exists():
        sequence += 1
        candidate = f"{repair_base}_{sequence}"
    if len(candidate) > 160:
        raise ValueError("repair coverage_id exceeds the supported length")
    return candidate


def promote_or_reuse_coverage(
    staging: Path,
    coverage_root: Path,
    coverage_manifest: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Promote staging or resume a coverage moved before marker publication."""
    if not coverage_root.exists():
        atomic_write_json(staging / "coverage.json", coverage_manifest)
        coverage_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, coverage_root)
        return coverage_manifest, False

    path = coverage_root / "coverage.json"
    if not path.is_file():
        raise ValueError(f"existing coverage has no manifest: {coverage_root}")
    existing = json.loads(path.read_text(encoding="utf-8"))
    identity_fields = (
        "status",
        "runtime_format",
        "group",
        "coverage_id",
        "latest_complete_run",
        "source_runs",
        "greenhouse_source_runs",
        "domains",
        "domain_grids",
        "static_sources",
        "public_start_utc",
        "local_day_start_utc",
        "public_end_utc",
        "public_hours",
        "historical_max_forecast_hour",
        "latest_max_forecast_hour",
        "short_run_count",
        "full_run_count",
        "source_run_max_forecast_hours",
        "producer_image",
    )
    for field in identity_fields:
        if existing.get(field) != coverage_manifest.get(field):
            raise ValueError(f"existing coverage identity mismatch for {field}: {coverage_root}")
    shutil.rmtree(staging)
    return existing, True


def load_coverage_manifests(coverages_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    manifests: list[tuple[Path, dict[str, Any]]] = []
    if not coverages_root.exists():
        return manifests
    for directory in coverages_root.iterdir():
        path = directory / "coverage.json"
        if not directory.is_dir() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") == "complete" and payload.get("latest_complete_run"):
            manifests.append((directory, payload))
    manifests.sort(
        key=lambda item: (
            str(item[1]["latest_complete_run"]),
            str(item[1].get("generated_at") or ""),
            str(item[1].get("coverage_id") or item[0].name),
        ),
        reverse=True,
    )
    return manifests


def protected_coverage_directories(output_root: Path, group: str) -> set[Path]:
    """Return exact native coverages that may still be open by the API."""
    protected: set[Path] = set()
    identifiers: dict[str, bool] = {}
    current_path = output_root / "groups" / group / "current" / "ready_for_processing.json"
    if current_path.is_file():
        current = json.loads(current_path.read_text(encoding="utf-8"))
        if current.get("runtime_format") == "openmeteo-native-v1":
            identifiers[str(current.get("coverage_id") or "")] = True
    applied_path = output_root / "groups" / group / "applied" / "current.json"
    if applied_path.is_file():
        applied = json.loads(applied_path.read_text(encoding="utf-8"))
        coverage_id = str(applied.get("coverage_id") or "")
        identifiers[coverage_id] = identifiers.get(coverage_id, False)
    root = output_root / "coverages" / group
    for coverage_id, required in identifiers.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", coverage_id):
            raise ValueError(f"invalid protected coverage_id for {group}")
        if not (root / coverage_id).exists() and not required:
            # Applied markers are advisory crash-safety state. Older runtime
            # generations may leave one behind after its immutable directory
            # was already retired. It cannot protect a directory that no
            # longer exists and must not block publication of a valid current
            # coverage. The current ready marker remains strict below.
            continue
        coverage = (root / coverage_id).resolve(strict=True)
        if coverage.parent != root.resolve(strict=True):
            raise ValueError("protected coverage resolves outside its group")
        manifest = json.loads((coverage / "coverage.json").read_text(encoding="utf-8"))
        if (
            manifest.get("status") != "complete"
            or manifest.get("group") != group
            or manifest.get("coverage_id") != coverage_id
        ):
            raise ValueError(f"invalid protected coverage manifest: {coverage_id}")
        protected.add(coverage)
    return protected


def retain_coverages_before_reload(
    coverage_root: Path,
    keep_coverages: int,
    protected: set[Path],
) -> None:
    """Bound history without deleting the exact snapshot still served by API."""
    manifests = load_coverage_manifests(coverage_root.parent)
    retained = {coverage_root.resolve(), *protected}
    retention_count = max(keep_coverages, 2)
    for candidate, _ in manifests:
        resolved = candidate.resolve()
        if resolved not in retained and len(retained) < retention_count:
            retained.add(resolved)
    for old_root, _ in manifests:
        resolved = old_root.resolve()
        if resolved.parent != coverage_root.parent.resolve():
            raise ValueError(f"refusing to prune coverage outside root: {resolved}")
        if resolved not in retained:
            shutil.rmtree(resolved)


def product_contract(coverage_id: str, domain_grids: dict[str, Any]) -> dict[str, Any]:
    return {
        "gfs013_surface": {
            "coverage_id": coverage_id,
            "runtime_domain": "ncep_gfs013",
            "grid": domain_grids["ncep_gfs013"],
        },
        "gfs025": {
            "coverage_id": coverage_id,
            "runtime_domain": "ncep_gfs025",
            "grid": domain_grids["ncep_gfs025"],
        },
        "gfs_pressure_profile": {
            "coverage_id": coverage_id,
            "runtime_domain": "ncep_gfs025",
            "grid": domain_grids["ncep_gfs025"],
        },
    }


def publish_gfs_coverage(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).resolve()
    staging = Path(args.staging_dir).resolve()
    ensure_staging_is_scoped(staging, output_root)
    if not staging.is_dir():
        raise ValueError(f"staging directory does not exist: {staging}")
    source_runs, source_run_max_forecast_hours = validate_gfs_window(args)
    if args.keep_coverages < 1:
        raise ValueError("keep_coverages must be positive")
    if args.public_hours < args.min_public_hours:
        raise ValueError(
            f"public window is {args.public_hours}h, required at least {args.min_public_hours}h"
        )

    required_by_domain = required_variables_by_domain(args)
    for domain in GFS_DOMAINS:
        read_latest(staging, domain, args.latest_run)
    for run, max_forecast_hour in zip(source_runs, source_run_max_forecast_hours):
        validate_gfs_retained_run(
            staging,
            run,
            max_forecast_hour,
            required_by_domain,
        )
    for domain in GFS_DOMAINS:
        latest_metadata = validate_run_metadata(
            staging,
            domain,
            args.latest_run,
            gfs_full_run_hours(args.latest_max_forecast_hour),
        )
        validate_runtime_variables(staging, domain, latest_metadata["variables"])

    domain_grids = gfs_domain_grids(
        getattr(args, "left_lon", 70.0),
        getattr(args, "right_lon", 140.0),
        getattr(args, "bottom_lat", 0.0),
        getattr(args, "top_lat", 58.0),
    )
    static_sources = {
        "copernicus_dem90": validate_dem_static(
            staging,
            int(getattr(args, "required_dem_lat_min", DEFAULT_DEM_LAT_MIN)),
            int(getattr(args, "required_dem_lat_max", DEFAULT_DEM_LAT_MAX)),
        )
    }

    revision = (getattr(args, "coverage_revision", None) or "").strip()
    if revision and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", revision):
        raise ValueError("coverage_revision must match [a-z0-9][a-z0-9_-]{0,31}")
    coverage_id = f"gfs_native_{args.latest_run}"
    if revision:
        coverage_id = f"{coverage_id}_{revision}"
    coverage_id = select_coverage_id_for_publish(
        output_root,
        "gfs",
        coverage_id,
        args.latest_run,
    )
    coverage_relative = Path("coverages") / "gfs" / coverage_id
    coverage_root = output_root / coverage_relative
    protected_coverages = protected_coverage_directories(output_root, "gfs")

    files, bytes_total = directory_stats(staging)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    producer_image = producer_image_ref()
    coverage_manifest = {
        "version": 1,
        "status": "complete",
        "runtime_format": "openmeteo-native-v1",
        "group": "gfs",
        "coverage_id": coverage_id,
        "latest_complete_run": args.latest_run,
        "source_runs": source_runs,
        "historical_max_forecast_hour": args.historical_max_forecast_hour,
        "latest_max_forecast_hour": args.latest_max_forecast_hour,
        "short_run_count": len(source_runs) - args.full_run_count,
        "full_run_count": args.full_run_count,
        "source_run_max_forecast_hours": source_run_max_forecast_hours,
        "public_start_utc": args.public_start_utc,
        "local_day_start_utc": args.local_day_start_utc,
        "public_end_utc": args.public_end_utc,
        "public_hours": args.public_hours,
        "domains": list(GFS_DOMAINS),
        "domain_grids": domain_grids,
        "static_sources": static_sources,
        "files": files,
        "bytes": bytes_total,
        "generated_at": generated_at,
    }
    if producer_image:
        coverage_manifest["producer_image"] = producer_image
    coverage_manifest, reused = promote_or_reuse_coverage(
        staging,
        coverage_root,
        coverage_manifest,
    )
    files = int(coverage_manifest["files"])
    bytes_total = int(coverage_manifest["bytes"])
    generated_at = str(coverage_manifest["generated_at"])

    release_id = coverage_id
    ready = {
        "version": 1,
        "status": "complete",
        "runtime_format": "openmeteo-native-v1",
        "group": "gfs",
        "release_id": release_id,
        "coverage_id": coverage_id,
        "latest_complete_run": args.latest_run,
        "source_runs": source_runs,
        "historical_max_forecast_hour": args.historical_max_forecast_hour,
        "latest_max_forecast_hour": args.latest_max_forecast_hour,
        "short_run_count": len(source_runs) - args.full_run_count,
        "full_run_count": args.full_run_count,
        "source_run_max_forecast_hours": source_run_max_forecast_hours,
        "public_start_utc": args.public_start_utc,
        "local_day_start_utc": args.local_day_start_utc,
        "public_end_utc": args.public_end_utc,
        "public_hours": args.public_hours,
        "coverage_path": coverage_relative.as_posix(),
        "products": product_contract(coverage_id, domain_grids),
        "domain_grids": domain_grids,
        "static_sources": static_sources,
        "files": files,
        "bytes": bytes_total,
        "generated_at": generated_at,
        "coverage_reused": reused,
    }
    if producer_image:
        ready["producer_image"] = producer_image
    atomic_write_json(
        output_root / "groups" / "gfs" / "releases" / f"{release_id}.json",
        ready,
    )
    atomic_symlink(Path("..") / coverage_relative, output_root / "current" / "gfs")
    atomic_write_json(
        output_root / "groups" / "gfs" / "current" / "ready_for_processing.json",
        ready,
    )

    retain_coverages_before_reload(
        coverage_root,
        args.keep_coverages,
        protected_coverages,
    )

    return ready


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish native Open-Meteo coverage")
    parser.add_argument("--group", choices=("gfs",), required=True)
    parser.add_argument("--staging-dir", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--latest-run", required=True)
    parser.add_argument("--source-runs", required=True)
    parser.add_argument("--full-run-count", type=int, default=2)
    parser.add_argument("--historical-max-forecast-hour", type=int, required=True)
    parser.add_argument("--latest-max-forecast-hour", type=int, required=True)
    parser.add_argument("--public-start-utc", required=True)
    parser.add_argument("--public-end-utc", required=True)
    parser.add_argument("--public-hours", type=int, required=True)
    parser.add_argument("--local-day-start-utc", required=True)
    parser.add_argument("--min-public-hours", type=int, default=300)
    parser.add_argument("--keep-coverages", type=int, default=2)
    parser.add_argument("--coverage-revision")
    parser.add_argument("--required-gfs013-variables", default=os.environ.get("WEATHER_GFS013_REQUIRED_DATA_RUN_VARIABLES", DEFAULT_GFS013_REQUIRED))
    parser.add_argument("--required-gfs025-variables", default=os.environ.get("WEATHER_GFS025_REQUIRED_DATA_RUN_VARIABLES", DEFAULT_GFS025_REQUIRED))
    parser.add_argument("--required-pressure-levels", default=os.environ.get("WEATHER_GFS_UPPER_LEVELS", DEFAULT_PRESSURE_LEVELS))
    parser.add_argument("--required-pressure-variables", default=os.environ.get("WEATHER_GFS_UPPER_LEVEL_VARIABLES", DEFAULT_PRESSURE_VARIABLES))
    parser.add_argument("--left-lon", type=float, default=float(os.environ.get("WEATHER_REGION_LEFT_LON", "70.0")))
    parser.add_argument("--right-lon", type=float, default=float(os.environ.get("WEATHER_REGION_RIGHT_LON", "140.0")))
    parser.add_argument("--bottom-lat", type=float, default=float(os.environ.get("WEATHER_REGION_BOTTOM_LAT", "0.0")))
    parser.add_argument("--top-lat", type=float, default=float(os.environ.get("WEATHER_REGION_TOP_LAT", "58.0")))
    parser.add_argument(
        "--required-dem-lat-min",
        type=int,
        default=int(os.environ.get("WEATHER_DEM_REQUIRED_LAT_MIN", str(DEFAULT_DEM_LAT_MIN))),
    )
    parser.add_argument(
        "--required-dem-lat-max",
        type=int,
        default=int(os.environ.get("WEATHER_DEM_REQUIRED_LAT_MAX", str(DEFAULT_DEM_LAT_MAX))),
    )
    args = parser.parse_args()

    try:
        ready = publish_gfs_coverage(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(ready, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
