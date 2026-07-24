#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
source "$APP_DIR/scripts/openmeteo_runtime_common.sh"
load_weather_env

ECMWF_ROOT="${WEATHER_ECMWF_ROOT:-$APP_DIR/data/ecmwf}"
DEM_ROOT="${WEATHER_OM_DEM_ROOT:-$APP_DIR/data/point}/copernicus_dem90"
IMAGE_NAME="${WEATHER_ECMWF_OPENMETEO_IMAGE:-weather-forecast-ecmwf}"
IMAGE_TAG="${WEATHER_ECMWF_OPENMETEO_TAG:-}"
IMAGE_REF="$IMAGE_NAME:$IMAGE_TAG"
PATCH_PATH="$APP_DIR/vendor/patches/open-meteo-ecmwf-regional.patch"
SOURCE_REVISION="$(git -C "$APP_DIR" rev-parse HEAD)"
INSTALL_ROOT="${WEATHER_ECMWF_API_INSTALL_ROOT:-/opt/1panel/apps/weather_ecmwf_api}"
UNIT_PATH=/etc/systemd/system/weather-ecmwf-api.service
PORT="${WEATHER_ECMWF_API_PORT:-18081}"

[[ "$(id -u)" -eq 0 ]] || { printf '%s\n' "Run as root" >&2; exit 2; }
[[ -n "$IMAGE_TAG" ]] || { printf '%s\n' "WEATHER_ECMWF_OPENMETEO_TAG is required" >&2; exit 2; }
[[ -d "$DEM_ROOT/static" ]] || { printf '%s\n' "Missing shared Copernicus DEM90 source" >&2; exit 1; }

PYTHONPATH="$APP_DIR/scripts" python3 "$APP_DIR/scripts/verify_ecmwf_runtime.py" \
  --root "$ECMWF_ROOT" \
  --image "$IMAGE_REF" \
  --patch "$PATCH_PATH" \
  --source-revision "$SOURCE_REVISION"

install -d -m 0755 "$INSTALL_ROOT"
printf '%s\n' "$SOURCE_REVISION" >"$INSTALL_ROOT/source-revision"
cat >"$INSTALL_ROOT/runtime.env" <<EOF
DATA_DIRECTORY=/app/data/
CACHE_SIZE=1GB
CACHE_META_SIZE=1MB
WEATHER_ECMWF_REGIONAL_GRID=true
WEATHER_ECMWF_STORAGE_LEFT_LON=${WEATHER_ECMWF_STORAGE_LEFT_LON:-68}
WEATHER_ECMWF_STORAGE_RIGHT_LON=${WEATHER_ECMWF_STORAGE_RIGHT_LON:-142}
WEATHER_ECMWF_STORAGE_BOTTOM_LAT=${WEATHER_ECMWF_STORAGE_BOTTOM_LAT:--2}
WEATHER_ECMWF_STORAGE_TOP_LAT=${WEATHER_ECMWF_STORAGE_TOP_LAT:-60}
EOF
chmod 0644 "$INSTALL_ROOT/runtime.env"

cat >"$UNIT_PATH" <<EOF
[Unit]
Description=Singapore Open-Meteo ECMWF IFS 0.25 API
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=5
TimeoutStopSec=45
ExecStartPre=/usr/bin/python3 $APP_DIR/scripts/verify_ecmwf_runtime.py --root $ECMWF_ROOT --image $IMAGE_REF --patch $PATCH_PATH --source-revision $SOURCE_REVISION
ExecStart=/usr/bin/docker run --rm --name weather-openmeteo-ecmwf-api --label weather.forecast.component=ecmwf-api --cpus=1.25 --cpu-shares=512 --memory=1536m --memory-swap=2048m --pids-limit=256 --security-opt=no-new-privileges:true --cap-drop=ALL --read-only --tmpfs /tmp:rw,noexec,nosuid,size=128m --tmpfs /app/data:rw,noexec,nosuid,size=1m --env-file $INSTALL_ROOT/runtime.env --volume $ECMWF_ROOT/current/ecmwf_ifs025:/app/data/ecmwf_ifs025:ro --volume $DEM_ROOT:/app/data/copernicus_dem90:ro --publish 127.0.0.1:$PORT:8080 $IMAGE_REF serve --env production --hostname 0.0.0.0 --port 8080
ExecStop=-/usr/bin/docker stop --time 30 weather-openmeteo-ecmwf-api

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$UNIT_PATH"
systemctl daemon-reload
systemctl enable weather-ecmwf-api.service
systemctl restart weather-ecmwf-api.service

for _ in $(seq 1 60); do
  if curl --fail --silent --show-error \
    --header 'Host: api.open-meteo.com' \
    "http://127.0.0.1:$PORT/v1/ecmwf?latitude=31.23&longitude=121.47&hourly=temperature_2m&forecast_days=1" \
    >/dev/null; then
    printf '%s\n' "weather-ecmwf-api.service ready revision=$SOURCE_REVISION image=$IMAGE_REF"
    exit 0
  fi
  sleep 2
done
systemctl status weather-ecmwf-api.service --no-pager >&2 || true
exit 1
