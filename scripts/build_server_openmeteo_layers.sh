#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEATHER_FORECAST_APP_DIR:-/opt/1panel/apps/weather_forecast_server}"
cd "$APP_DIR"

bash scripts/build_openmeteo_gfs_layers.sh
bash scripts/build_openmeteo_cams_layers.sh

PUBLIC_DATA_DIR="${WEATHER_OPENMETEO_PUBLIC_DATA_DIR:-$APP_DIR/data/public}"
mkdir -p "$PUBLIC_DATA_DIR/openmeteo_layers"
cp -f "$APP_DIR/config/weather_layer_catalog.json" "$PUBLIC_DATA_DIR/openmeteo_layers/weather_layer_catalog.json"
