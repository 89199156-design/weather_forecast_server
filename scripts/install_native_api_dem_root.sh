#!/usr/bin/env bash
set -Eeuo pipefail

RUNTIME_ROOT="${WEATHER_FORECAST_RUNTIME_ROOT:-/opt/1panel/apps/weather_forecast_server}"
API_SERVICE="${WEATHER_OM_API_SERVICE:-weather-om-api.service}"
DEM_ROOT="${WEATHER_OM_DEM_ROOT:-${WEATHER_OPENMETEO_DATA_DIR:-$RUNTIME_ROOT/data/point}}"
DEM_LAT_MIN="${WEATHER_DEM_REQUIRED_LAT_MIN:-0}"
DEM_LAT_MAX="${WEATHER_DEM_REQUIRED_LAT_MAX:-58}"
DROPIN_PATH="${WEATHER_OM_API_DEM_DROPIN_PATH:-/etc/systemd/system/${API_SERVICE}.d/20-dem-root.conf}"
HEALTHCHECK_URL="${WEATHER_OM_API_HEALTHCHECK_URL:-http://127.0.0.1:8088/v1/forecast?latitude=31.2304&longitude=121.4737&hourly=temperature_2m&forecast_hours=1&timezone=GMT}"
INSTALL_LOCK="${WEATHER_OM_API_CONFIG_LOCK_FILE:-/tmp/weather_om_api_config.lock}"
SUDO_BIN="${WEATHER_SUDO_BIN:-sudo}"
SYSTEMCTL_BIN="${WEATHER_SYSTEMCTL_BIN:-systemctl}"
CURL_BIN="${WEATHER_CURL_BIN:-curl}"

DROPIN_DIR="$(dirname "$DROPIN_PATH")"
DROPIN_NAME="$(basename "$DROPIN_PATH")"
LOCAL_TEMP=""
REMOTE_TEMP=""
PREVIOUS_EXISTS=false
CONFIG_CHANGED=false
INSTALL_SUCCEEDED=false

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "$command_name is required" >&2
    exit 1
  fi
}

validate_absolute_path() {
  local name="$1"
  local value="$2"
  if [[ "$value" != /* || "$value" == *[!A-Za-z0-9_./:@-]* ]]; then
    echo "$name must be an absolute path using systemd-safe characters: $value" >&2
    exit 1
  fi
}

sudo_systemctl() {
  "$SUDO_BIN" -n "$SYSTEMCTL_BIN" "$@"
}

atomic_install() {
  local source="$1"
  local source_sha remote_sha
  "$SUDO_BIN" -n install -d -m 0755 "$DROPIN_DIR"
  REMOTE_TEMP="$("$SUDO_BIN" -n mktemp "$DROPIN_DIR/.${DROPIN_NAME}.tmp.XXXXXX")"
  "$SUDO_BIN" -n install -m 0644 "$source" "$REMOTE_TEMP"
  source_sha="$(sha256sum "$source" | awk '{print $1}')"
  remote_sha="$("$SUDO_BIN" -n sha256sum "$REMOTE_TEMP" | awk '{print $1}')"
  if [[ "$source_sha" != "$remote_sha" ]]; then
    echo "systemd drop-in staging checksum mismatch" >&2
    return 1
  fi
  "$SUDO_BIN" -n mv -Tf "$REMOTE_TEMP" "$DROPIN_PATH"
  REMOTE_TEMP=""
}

running_dem_root() {
  local pid="$1"
  "$SUDO_BIN" -n cat "/proc/$pid/environ" \
    | tr '\0' '\n' \
    | sed -n 's/^OM_DEM_ROOT=//p'
}

restore_previous_config() {
  set +e
  if [[ "$PREVIOUS_EXISTS" == true ]]; then
    atomic_install "$LOCAL_TEMP/previous.conf"
  else
    "$SUDO_BIN" -n rm -f -- "$DROPIN_PATH"
  fi
  sudo_systemctl daemon-reload
  sudo_systemctl restart "$API_SERVICE"
}

cleanup() {
  local rc=$?
  trap - EXIT
  if [[ "$INSTALL_SUCCEEDED" != true && "$CONFIG_CHANGED" == true ]]; then
    echo "DEM root installation failed; restoring the previous systemd configuration" >&2
    restore_previous_config
  fi
  if [[ -n "$REMOTE_TEMP" ]]; then
    "$SUDO_BIN" -n rm -f -- "$REMOTE_TEMP" 2>/dev/null || true
  fi
  if [[ -n "$LOCAL_TEMP" && -d "$LOCAL_TEMP" ]]; then
    rm -rf -- "$LOCAL_TEMP"
  fi
  exit "$rc"
}

trap cleanup EXIT

for command_name in awk cat flock install mktemp mv rm sed sha256sum tr "$SUDO_BIN" "$SYSTEMCTL_BIN" "$CURL_BIN"; do
  require_command "$command_name"
done
if ! [[ "$API_SERVICE" =~ ^[A-Za-z0-9_.@-]+\.service$ ]]; then
  echo "invalid systemd service name: $API_SERVICE" >&2
  exit 1
fi
validate_absolute_path WEATHER_OM_DEM_ROOT "$DEM_ROOT"
validate_absolute_path WEATHER_OM_API_DEM_DROPIN_PATH "$DROPIN_PATH"
validate_absolute_path WEATHER_OM_API_CONFIG_LOCK_FILE "$INSTALL_LOCK"
if ! [[ "$DEM_LAT_MIN" =~ ^-?[0-9]+$ && "$DEM_LAT_MAX" =~ ^-?[0-9]+$ ]]; then
  echo "DEM latitude bounds must be integers" >&2
  exit 1
fi
if (( DEM_LAT_MIN > DEM_LAT_MAX )); then
  echo "DEM latitude minimum exceeds maximum" >&2
  exit 1
fi
for ((latitude = DEM_LAT_MIN; latitude <= DEM_LAT_MAX; latitude++)); do
  chunk="$DEM_ROOT/copernicus_dem90/static/lat_${latitude}.om"
  if [[ ! -s "$chunk" ]]; then
    echo "missing required Copernicus DEM90 chunk: $chunk" >&2
    exit 1
  fi
done

exec 9>"$INSTALL_LOCK"
flock -n 9 || {
  echo "another native API systemd installation is active" >&2
  exit 1
}

LOCAL_TEMP="$(mktemp -d)"
cat >"$LOCAL_TEMP/candidate.conf" <<EOF
[Service]
Environment="OM_DEM_ROOT=$DEM_ROOT"
ReadOnlyPaths=$DEM_ROOT/copernicus_dem90
EOF

if "$SUDO_BIN" -n test -L "$DROPIN_PATH"; then
  echo "refusing symlink systemd drop-in: $DROPIN_PATH" >&2
  exit 1
fi
if "$SUDO_BIN" -n test -e "$DROPIN_PATH"; then
  if ! "$SUDO_BIN" -n test -f "$DROPIN_PATH"; then
    echo "systemd drop-in is not a regular file: $DROPIN_PATH" >&2
    exit 1
  fi
  PREVIOUS_EXISTS=true
  "$SUDO_BIN" -n cat "$DROPIN_PATH" >"$LOCAL_TEMP/previous.conf"
fi

candidate_sha="$(sha256sum "$LOCAL_TEMP/candidate.conf" | awk '{print $1}')"
installed_sha=""
if [[ "$PREVIOUS_EXISTS" == true ]]; then
  installed_sha="$("$SUDO_BIN" -n sha256sum "$DROPIN_PATH" | awk '{print $1}')"
fi
if [[ "$candidate_sha" != "$installed_sha" ]]; then
  atomic_install "$LOCAL_TEMP/candidate.conf"
  CONFIG_CHANGED=true
  sudo_systemctl daemon-reload
fi

need_restart="$CONFIG_CHANGED"
main_pid=""
if [[ "$need_restart" != true ]] && sudo_systemctl is-active --quiet "$API_SERVICE"; then
  main_pid="$(sudo_systemctl show "$API_SERVICE" --property=MainPID --value)"
  if [[ ! "$main_pid" =~ ^[1-9][0-9]*$ ]] \
    || [[ "$(running_dem_root "$main_pid")" != "$DEM_ROOT" ]]; then
    need_restart=true
  fi
else
  need_restart=true
fi
if [[ "$need_restart" == true ]]; then
  sudo_systemctl daemon-reload
  sudo_systemctl restart "$API_SERVICE"
fi

sudo_systemctl is-active --quiet "$API_SERVICE"
main_pid="$(sudo_systemctl show "$API_SERVICE" --property=MainPID --value)"
if [[ ! "$main_pid" =~ ^[1-9][0-9]*$ ]]; then
  echo "systemd returned an invalid MainPID for $API_SERVICE: $main_pid" >&2
  false
fi
actual_dem_root="$(running_dem_root "$main_pid")"
if [[ "$actual_dem_root" != "$DEM_ROOT" ]]; then
  echo "running API OM_DEM_ROOT mismatch: expected=$DEM_ROOT actual=$actual_dem_root" >&2
  false
fi
installed_sha="$("$SUDO_BIN" -n sha256sum "$DROPIN_PATH" | awk '{print $1}')"
if [[ "$installed_sha" != "$candidate_sha" ]]; then
  echo "installed systemd drop-in checksum mismatch" >&2
  false
fi
"$CURL_BIN" --fail --silent --show-error \
  --connect-timeout 2 --max-time 30 --retry 5 --retry-delay 1 --retry-connrefused \
  "$HEALTHCHECK_URL" >/dev/null

INSTALL_SUCCEEDED=true
echo "service=$API_SERVICE dem_root=$DEM_ROOT dropin=$DROPIN_PATH changed=$CONFIG_CHANGED running_pid=$main_pid"
