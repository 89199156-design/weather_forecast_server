#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env

CONFIG_PATH="${WEATHER_ECMWF_OPENRESTY_SITE_CONFIG:-/opt/1panel/apps/openresty/openresty/conf/conf.d/weather.xiaoztech.com.conf}"
DEFAULT_CONFIG_PATH="${WEATHER_OPENRESTY_DEFAULT_CONFIG:-/opt/1panel/apps/openresty/openresty/conf/conf.d/00.default.conf}"
CONTAINER="${WEATHER_ECMWF_OPENRESTY_CONTAINER:-1Panel-openresty-XU4Q}"
PORT="${WEATHER_ECMWF_API_PORT:-18081}"
NATIVE_PORT="${WEATHER_OM_API_PORT:-8088}"
BEGIN_MARKER="    # BEGIN weather-forecast ECMWF API (managed)"
END_MARKER="    # END weather-forecast ECMWF API (managed)"

[[ "$(id -u)" -eq 0 ]] || { printf '%s\n' "Run as root" >&2; exit 2; }
[[ -f "$CONFIG_PATH" ]] || { printf '%s\n' "Missing OpenResty site config: $CONFIG_PATH" >&2; exit 1; }
[[ -f "$DEFAULT_CONFIG_PATH" ]] || { printf '%s\n' "Missing OpenResty default config: $DEFAULT_CONFIG_PATH" >&2; exit 1; }
[[ "$PORT" =~ ^[0-9]+$ ]] || { printf '%s\n' "Invalid ECMWF API port" >&2; exit 2; }
[[ "$NATIVE_PORT" =~ ^[0-9]+$ ]] || { printf '%s\n' "Invalid native API port" >&2; exit 2; }

python3 - "$CONFIG_PATH" "$DEFAULT_CONFIG_PATH" "$PORT" "$NATIVE_PORT" "$BEGIN_MARKER" "$END_MARKER" <<'PY'
import os
from pathlib import Path
import sys

site_path = Path(sys.argv[1])
default_path = Path(sys.argv[2])
port = int(sys.argv[3])
native_port = int(sys.argv[4])
begin = sys.argv[5]
end = sys.argv[6]
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
        "    location = /v1/gfs {",
        f"        proxy_pass http://127.0.0.1:{native_port};",
        "        proxy_set_header Host $host;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "        proxy_http_version 1.1;",
        "        proxy_connect_timeout 5s;",
        "        proxy_read_timeout 180s;",
        "        proxy_send_timeout 180s;",
        "    }",
        "    location = /v1/cams {",
        f"        proxy_pass http://127.0.0.1:{native_port};",
        "        proxy_set_header Host $host;",
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

def install(path: Path, anchor: str) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(begin) > 1 or source.count(end) > 1:
        raise SystemExit(f"duplicate managed model API proxy markers: {path}")
    if begin in source or end in source:
        if begin not in source or end not in source:
            raise SystemExit(f"incomplete managed model API proxy block: {path}")
        before, remainder = source.split(begin, 1)
        _old, after = remainder.split(end, 1)
        updated = before + block + after
    else:
        if source.count(anchor) != 1:
            raise SystemExit(f"OpenResty config insertion anchor is not unique: {path}")
        updated = source.replace(anchor, block + "\n" + anchor, 1)
    if updated != source:
        temporary = path.with_name(f".{path.name}.model-api.{os.getpid()}.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)

install(site_path, "    location ^~ /.well-known/acme-challenge {")
install(default_path, "    index 404.html;")
PY

docker exec "$CONTAINER" openresty -t
docker exec "$CONTAINER" openresty -s reload

wait_for_route() {
  local url="$1"
  local attempt
  for attempt in {1..10}; do
    if curl --fail --silent --show-error \
      --header 'Host: 43.156.81.216' \
      "$url" \
      >/dev/null; then
      return 0
    fi
    sleep 1
  done
  printf 'OpenResty route did not become ready: %s\n' "$url" >&2
  return 1
}

wait_for_route \
  "http://127.0.0.1/v1/ecmwf?latitude=31.23&longitude=121.47&hourly=temperature_2m&forecast_days=1"
wait_for_route \
  "http://127.0.0.1/v1/gfs?latitude=31.23&longitude=121.47&hourly=temperature_2m&forecast_hours=1"
wait_for_route \
  "http://127.0.0.1/v1/cams?latitude=31.23&longitude=121.47&hourly=pm2_5&forecast_hours=1"
printf '%s\n' \
  "OpenResty model API routes ready paths=/v1/gfs,/v1/ecmwf,/v1/cams upstreams=127.0.0.1:$NATIVE_PORT,127.0.0.1:$PORT"
