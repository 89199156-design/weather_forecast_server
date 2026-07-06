#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


UTC = timezone.utc
DEFAULT_CAMS_FTP_PROBE_WORKERS = 1

CAMS_GLOBAL_META: dict[str, tuple[str, bool]] = {
    "pm2_5": ("pm2p5", False),
    "pm10": ("pm10", False),
    "aerosol_optical_depth": ("aod550", False),
    "dust": ("aermr06", True),
    "carbon_monoxide": ("co", True),
    "nitrogen_dioxide": ("no2", True),
    "ozone": ("go3", True),
    "sulphur_dioxide": ("so2", True),
}


@dataclass(frozen=True)
class ProbeResult:
    url: str
    ok: bool
    detail: str


def parse_iso_z(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def read_local_latest(data_dir: Path) -> datetime | None:
    path = data_dir / "data_run" / "cams_global" / "latest.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return parse_iso_z(payload.get("reference_time"))


def floor_to_cams_run(now: datetime) -> datetime:
    now = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return now.replace(hour=12 if now.hour >= 12 else 0)


def candidate_runs(now: datetime, local_latest: datetime | None, lookback_hours: int) -> list[datetime]:
    first = floor_to_cams_run(now)
    runs = [first - timedelta(hours=12 * offset) for offset in range((lookback_hours // 12) + 1)]
    if local_latest is None:
        return runs
    return [run for run in runs if run > local_latest]


def parse_variables(value: str) -> list[str]:
    variables = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [variable for variable in variables if variable not in CAMS_GLOBAL_META]
    if unknown:
        known = ",".join(sorted(CAMS_GLOBAL_META))
        raise SystemExit(f"Unsupported CAMS FTP probe variables: {','.join(unknown)}. Known: {known}")
    return variables


def probe_forecast_hours(value: str | None, max_forecast_hour: int) -> list[int]:
    if value:
        hours = [int(item.strip()) for item in value.split(",") if item.strip()]
    else:
        hours = list(range(max_forecast_hour + 1))
    bounded = [hour for hour in hours if 0 <= hour <= max_forecast_hour]
    if not bounded:
        raise SystemExit("CAMS FTP probe forecast hours must include at least one hour in range")
    return sorted(set(bounded))


def cams_urls(run: datetime, variables: list[str], forecast_hours: list[int]) -> list[str]:
    date_run = run.strftime("%Y%m%d%H")
    urls: list[str] = []
    for forecast_hour in forecast_hours:
        for variable in variables:
            gribname, is_multi_level = CAMS_GLOBAL_META[variable]
            level_type = "ml137" if is_multi_level else "sfc"
            directory = "CAMS_GLOBAL_ADDITIONAL" if is_multi_level else "CAMS_GLOBAL"
            filename = f"z_cams_c_ecmf_{date_run}0000_prod_fc_{level_type}_{forecast_hour:03d}_{gribname}.nc"
            urls.append(f"https://aux.ecmwf.int/ecpds/data/file/{directory}/{date_run}/{filename}")
    return urls


def auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def check_url(url: str, authorization: str, timeout_seconds: float) -> ProbeResult:
    headers = {
        "Authorization": authorization,
        "Range": "bytes=0-0",
        "User-Agent": "weather-forecast-server-cams-probe/1.0",
    }
    request = Request(url, headers=headers, method="HEAD")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
    except HTTPError as exc:
        return ProbeResult(url=url, ok=False, detail=f"http_{exc.code}")
    except (TimeoutError, URLError, OSError) as exc:
        return ProbeResult(url=url, ok=False, detail=exc.__class__.__name__)

    if status not in (200, 206):
        return ProbeResult(url=url, ok=False, detail=f"http_{status}")
    return ProbeResult(url=url, ok=True, detail="ok")


def run_complete(
    run: datetime,
    variables: list[str],
    forecast_hours: list[int],
    timeout_seconds: float,
    workers: int,
    authorization: str,
) -> tuple[bool, list[ProbeResult]]:
    urls = cams_urls(run, variables, forecast_hours)
    failures: list[ProbeResult] = []
    batch_size = max(1, workers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for index in range(0, len(urls), batch_size):
            futures = [
                executor.submit(check_url, url, authorization, timeout_seconds)
                for url in urls[index : index + batch_size]
            ]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if not result.ok:
                    failures.append(result)
            if failures:
                return False, failures
    return not failures, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe ECMWF CAMS FTP/ECPDS files and report the newest complete run.")
    parser.add_argument("--data-dir", default="./data/point")
    parser.add_argument("--variables", default=os.environ.get("WEATHER_CAMS_VARIABLES", "pm2_5,pm10,aerosol_optical_depth,dust,carbon_monoxide,nitrogen_dioxide,ozone,sulphur_dioxide"))
    parser.add_argument("--max-forecast-hour", type=int, default=120)
    parser.add_argument("--probe-forecast-hours", default=os.environ.get("WEATHER_CAMS_PROBE_FORECAST_HOURS"))
    parser.add_argument("--lookback-hours", type=int, default=72)
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--workers", type=int, default=DEFAULT_CAMS_FTP_PROBE_WORKERS)
    parser.add_argument("--user", default=os.environ.get("WEATHER_CAMS_FTP_USER", ""))
    parser.add_argument("--password", default=os.environ.get("WEATHER_CAMS_FTP_PASSWORD", ""))
    args = parser.parse_args()

    if not args.user or not args.password:
        print("NOT_READY missing_ftp_credentials", file=sys.stderr)
        return 1

    variables = parse_variables(args.variables)
    forecast_hours = probe_forecast_hours(args.probe_forecast_hours, args.max_forecast_hour)
    local_latest = read_local_latest(Path(args.data_dir))
    authorization = auth_header(args.user, args.password)
    now = datetime.now(UTC)

    for run in candidate_runs(now, local_latest, args.lookback_hours):
        complete, failures = run_complete(
            run=run,
            variables=variables,
            forecast_hours=forecast_hours,
            timeout_seconds=args.timeout_seconds,
            workers=args.workers,
            authorization=authorization,
        )
        if complete:
            print(f"READY {run.strftime('%Y%m%d%H')} {run.strftime('%Y-%m-%dT%H:00:00Z')}")
            return 0
        first = failures[0] if failures else None
        if first is not None:
            print(f"NOT_READY {run.strftime('%Y%m%d%H')} {first.detail} {first.url}", file=sys.stderr)

    latest = local_latest.strftime("%Y-%m-%dT%H:00:00Z") if local_latest else "none"
    print(f"NOT_READY local_latest={latest}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
