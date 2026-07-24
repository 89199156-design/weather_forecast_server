#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env

CONFIG_PATH="${WEATHER_ECMWF_OPENRESTY_SITE_CONFIG:-/opt/1panel/apps/openresty/openresty/conf/conf.d/weather.xiaoztech.com.conf}"
CONTAINER="${WEATHER_ECMWF_OPENRESTY_CONTAINER:-1Panel-openresty-XU4Q}"
PORT="${WEATHER_ECMWF_API_PORT:-18081}"
BEGIN_MARKER="    # BEGIN weather-forecast ECMWF API (managed)"
END_MARKER="    # END weather-forecast ECMWF API (managed)"

[[ "$(id -u)" -eq 0 ]] || { printf '%s\n' "Run as root" >&2; exit 2; }
[[ -f "$CONFIG_PATH" ]] || { printf '%s\n' "Missing OpenResty site config: $CONFIG_PATH" >&2; exit 1; }
[[ "$PORT" =~ ^[0-9]+$ ]] || { printf '%s\n' "Invalid ECMWF API port" >&2; exit 2; }

python3 - "$CONFIG_PATH" "$PORT" "$BEGIN_MARKER" "$END_MARKER" <<'PY'
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
port = int(sys.argv[2])
begin = sys.argv[3]
end = sys.argv[4]
source = path.read_text(encoding="utf-8")
block = "\n".join(
    (
        begin,
        "    location ^~ /v1/ecmwf {",
        f"        proxy_pass http://127.0.0.1:{port};",
        "        proxy_set_header Host api.open-meteo.com;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "        proxy_http_version 1.1;",
        "        proxy_connect_timeout 5s;",
        "        proxy_read_timeout 180s;",
        "        proxy_send_timeout 180s;",
        "    }",
        end,
    )
)
if source.count(begin) > 1 or source.count(end) > 1:
    raise SystemExit("duplicate managed ECMWF proxy markers")
if begin in source or end in source:
    if begin not in source or end not in source:
        raise SystemExit("incomplete managed ECMWF proxy block")
    before, remainder = source.split(begin, 1)
    _old, after = remainder.split(end, 1)
    updated = before + block + after
else:
    anchor = "    location ^~ /.well-known/acme-challenge {"
    if source.count(anchor) != 1:
        raise SystemExit("OpenResty site config insertion anchor is not unique")
    updated = source.replace(anchor, block + "\n" + anchor, 1)
if updated != source:
    temporary = path.with_name(f".{path.name}.ecmwf.{os.getpid()}.tmp")
    temporary.write_text(updated, encoding="utf-8")
    os.chmod(temporary, path.stat().st_mode)
    os.replace(temporary, path)
PY

docker exec "$CONTAINER" openresty -t
docker exec "$CONTAINER" openresty -s reload

curl --fail --silent --show-error \
  --header 'Host: weather.xiaoztech.com' \
  "http://127.0.0.1/v1/ecmwf?latitude=31.23&longitude=121.47&hourly=temperature_2m&forecast_days=1" \
  >/dev/null
printf '%s\n' "OpenResty ECMWF API route ready path=/v1/ecmwf upstream=127.0.0.1:$PORT"
