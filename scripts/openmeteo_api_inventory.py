#!/usr/bin/env python3
"""Extract GFS/CAMS API inventory from the vendored Open-Meteo source tree."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_enum_cases(source: str, enum_name: str) -> list[str]:
    lines = source.splitlines()
    enum_start = re.compile(rf"^\s*enum\s+{re.escape(enum_name)}\b")
    case_line = re.compile(r"^\s*case\s+(.+)$")
    cases: list[str] = []
    in_enum = False
    depth = 0

    for line in lines:
        if not in_enum:
            if enum_start.search(line):
                in_enum = True
                depth += line.count("{") - line.count("}")
            continue

        if depth == 1:
            match = case_line.match(line)
            if match:
                declaration = match.group(1).split("//", 1)[0].strip()
                for item in declaration.split(","):
                    name = item.strip().split(" ", 1)[0]
                    if name and not name.startswith("."):
                        cases.append(name)

        depth += line.count("{") - line.count("}")
        if depth <= 0:
            break

    if not cases:
        raise ValueError(f"no cases found for enum {enum_name}")
    return cases


def extract_gfs_domain_levels(source: str, domain: str) -> list[int]:
    pattern = re.compile(rf"case\s+\.{re.escape(domain)}:\s*(?://[^\n]*\n|\s)*return\s+\[([^\]]*)\]", re.S)
    match = pattern.search(source)
    if not match:
        raise ValueError(f"no level list found for GfsDomain.{domain}")
    raw_items = match.group(1).split(",")
    levels = []
    for item in raw_items:
        item = item.split("//", 1)[0].strip()
        if item:
            levels.append(int(item))
    return levels


def pressure_api_names(variable_types: list[str], levels: list[int]) -> list[str]:
    return [f"{variable}_{level}hPa" for variable in variable_types for level in levels]


def build_inventory(repo_root: Path) -> dict[str, Any]:
    hourly_source = read_text(repo_root / "vendor" / "open-meteo" / "Sources" / "App" / "Controllers" / "VariableHourly.swift")
    gfs_variable_source = read_text(repo_root / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsVariable.swift")
    gfs_domain_source = read_text(repo_root / "vendor" / "open-meteo" / "Sources" / "App" / "Gfs" / "GfsDomain.swift")
    cams_domain_source = read_text(repo_root / "vendor" / "open-meteo" / "Sources" / "App" / "Cams" / "CamsDomain.swift")
    cams_reader_source = read_text(repo_root / "vendor" / "open-meteo" / "Sources" / "App" / "Cams" / "CamsReader.swift")

    forecast_surface = extract_enum_cases(hourly_source, "ForecastSurfaceVariable")
    forecast_pressure_types = extract_enum_cases(hourly_source, "ForecastPressureVariableType")
    gfs_surface = extract_enum_cases(gfs_variable_source, "GfsSurfaceVariable")
    gfs_pressure_types = extract_enum_cases(gfs_variable_source, "GfsPressureVariableType")
    gfs025_levels = extract_gfs_domain_levels(gfs_domain_source, "gfs025")
    cams_raw = extract_enum_cases(cams_domain_source, "CamsVariable")
    cams_derived = extract_enum_cases(cams_reader_source, "CamsVariableDerived")

    return {
        "forecast": {
            "endpoint": "/v1/forecast",
            "model": "gfs_global",
            "source_enums": {
                "surface": "ForecastSurfaceVariable",
                "pressure": "ForecastPressureVariableType",
            },
            "surface_api_variables": forecast_surface,
            "pressure_api_variables": pressure_api_names(forecast_pressure_types, gfs025_levels),
            "counts": {
                "surface_api_variables": len(forecast_surface),
                "pressure_api_variables": len(forecast_pressure_types) * len(gfs025_levels),
            },
        },
        "gfs_runtime_data": {
            "reader_model": "gfs_global",
            "required_domains": ["gfs013", "gfs025"],
            "gfs025_pressure_levels_hpa": gfs025_levels,
            "surface_variables": gfs_surface,
            "pressure_variables": pressure_api_names(gfs_pressure_types, gfs025_levels),
            "counts": {
                "surface_variables": len(gfs_surface),
                "pressure_variables": len(gfs_pressure_types) * len(gfs025_levels),
            },
        },
        "air_quality": {
            "endpoint": "/v1/air-quality",
            "domain": "cams_global",
            "source_enums": {
                "raw": "CamsVariable",
                "derived": "CamsVariableDerived",
            },
            "raw_variables": cams_raw,
            "derived_variables": cams_derived,
            "counts": {
                "raw_variables": len(cams_raw),
                "derived_variables": len(cams_derived),
            },
        },
        "runtime_download_requirements": [
            {"command": "download-gfs", "domain": "gfs013", "levels": "surface"},
            {"command": "download-gfs", "domain": "gfs025", "levels": "surface"},
            {"command": "download-gfs", "domain": "gfs025", "levels": "surface+upper"},
            {"command": "download-cams", "domain": "cams_global", "levels": "surface"},
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a GFS/CAMS API inventory from vendored Open-Meteo source.")
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", default="-", help="Output JSON path, or '-' for stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inventory = build_inventory(Path(args.repo_root))
    payload = json.dumps(inventory, indent=2, ensure_ascii=False) + "\n"
    if args.output == "-":
        print(payload, end="")
    else:
        Path(args.output).write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
