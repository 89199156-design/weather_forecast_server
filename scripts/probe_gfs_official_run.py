#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from gfs_schedule import gfs_forecast_hours


UTC = timezone.utc


@dataclass(frozen=True)
class ProbeResult:
    url: str
    ok: bool
    detail: str


def parse_iso_z(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def read_local_state(data_dir: Path) -> tuple[datetime | None, set[str]]:
    group_ready = data_dir / "groups" / "gfs" / "current" / "ready_for_processing.json"
    if group_ready.is_file():
        try:
            payload = json.loads(group_ready.read_text(encoding="utf-8"))
            if payload.get("status") == "complete":
                run = str(payload.get("latest_complete_run") or "")
                source_runs = payload.get("source_runs")
                if run and isinstance(source_runs, list):
                    return (
                        datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=UTC),
                        set(str(value) for value in source_runs),
                    )
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    refs: list[datetime] = []
    for domain in ("ncep_gfs013", "ncep_gfs025"):
        path = data_dir / "data_run" / domain / "latest.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        ref = parse_iso_z(payload.get("reference_time"))
        if ref is not None:
            refs.append(ref)
    if not refs:
        return None, set()
    return min(refs), set()


def read_local_latest(data_dir: Path) -> datetime | None:
    return read_local_state(data_dir)[0]


def floor_to_gfs_run(now: datetime) -> datetime:
    now = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return now.replace(hour=(now.hour // 6) * 6)


def candidate_runs(
    now: datetime,
    local_latest: datetime | None,
    lookback_hours: int,
    local_source_runs: set[str] | None = None,
    source_run_count: int = 5,
) -> list[datetime]:
    first = floor_to_gfs_run(now)
    runs = [first - timedelta(hours=6 * offset) for offset in range((lookback_hours // 6) + 1)]
    if local_latest is None:
        return runs
    candidates = [run for run in runs if run > local_latest]
    local_source_runs = local_source_runs or set()
    expected = {
        (local_latest - timedelta(hours=6 * offset)).strftime("%Y%m%d%H")
        for offset in range(source_run_count)
    }
    if expected - local_source_runs and local_latest in runs:
        candidates.append(local_latest)
    return sorted(set(candidates), reverse=True)


def gfs_urls(run: datetime, max_forecast_hour: int) -> list[str]:
    ymd = run.strftime("%Y%m%d")
    hh = run.strftime("%H")
    base = f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{ymd}/{hh}/atmos"
    urls: list[str] = []
    available_hours = set(gfs_forecast_hours(max_forecast_hour))
    sentinel_hours = sorted(
        available_hours.intersection({0, 5, 120, 123, max_forecast_hour})
    )
    for forecast_hour in sentinel_hours:
        fff = f"{forecast_hour:03d}"
        urls.append(f"{base}/gfs.t{hh}z.sfluxgrbf{fff}.grib2.idx")
        urls.append(f"{base}/gfs.t{hh}z.pgrb2.0p25.f{fff}.idx")
        urls.append(f"{base}/gfs.t{hh}z.pgrb2b.0p25.f{fff}.idx")
    return urls


def check_url(url: str, timeout_seconds: float) -> ProbeResult:
    request = Request(url, headers={"Range": "bytes=0-2047", "User-Agent": "weather-forecast-server-gfs-probe/1.0"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            data = response.read(2048)
    except HTTPError as exc:
        return ProbeResult(url=url, ok=False, detail=f"http_{exc.code}")
    except (TimeoutError, URLError, OSError) as exc:
        return ProbeResult(url=url, ok=False, detail=exc.__class__.__name__)

    if status not in (200, 206):
        return ProbeResult(url=url, ok=False, detail=f"http_{status}")
    if not data:
        return ProbeResult(url=url, ok=False, detail="empty")
    return ProbeResult(url=url, ok=True, detail="ok")


def run_complete(run: datetime, max_forecast_hour: int, timeout_seconds: float, workers: int) -> tuple[bool, list[ProbeResult]]:
    urls = gfs_urls(run, max_forecast_hour)
    failures: list[ProbeResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(check_url, url, timeout_seconds) for url in urls]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if not result.ok:
                failures.append(result)
                if len(failures) >= 20:
                    return False, failures
    return not failures, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe GFS boundary/sentinel index files and report the newest complete run.")
    parser.add_argument("--data-dir", default="./data/point")
    parser.add_argument("--max-forecast-hour", type=int, default=384)
    parser.add_argument("--lookback-hours", type=int, default=36)
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    try:
        gfs_forecast_hours(args.max_forecast_hour)
    except ValueError as exc:
        parser.error(str(exc))

    now = datetime.now(UTC)
    data_dir = Path(args.data_dir)
    local_latest, local_source_runs = read_local_state(data_dir)

    for run in candidate_runs(now, local_latest, args.lookback_hours, local_source_runs):
        complete, failures = run_complete(run, args.max_forecast_hour, args.timeout_seconds, args.workers)
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
