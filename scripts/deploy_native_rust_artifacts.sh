#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
API_ROOT="${WEATHER_OM_API_ROOT:-/opt/1panel/apps/weather_om_api}"
WEBP_ROOT="${WEATHER_OM_WEBP_ROOT:-/opt/1panel/apps/weather_om_webp}"
API_SERVICE="${WEATHER_OM_API_SERVICE:-weather-om-api.service}"
API_HEALTHCHECK_URL="${WEATHER_OM_API_HEALTHCHECK_URL:-http://127.0.0.1:8088/v1/forecast?latitude=31.2304&longitude=121.4737&hourly=temperature_2m&forecast_hours=1&timezone=GMT}"
BUILD_ROOT="${WEATHER_RUST_BUILD_ROOT:-$APP_ROOT/.build/native-rust-artifacts}"
DEPLOY_LOCK_GROUP="${WEATHER_OM_DEPLOY_LOCK_GROUP:-$(id -gn)}"
GFS_SCHEDULE_LOCK="${WEATHER_OPENMETEO_GFS_PROBE_LOCK_FILE:-/tmp/weather_openmeteo_gfs_probe.lock}"
GFS_CYCLE_LOCK="${WEATHER_OPENMETEO_GFS_LOCK_FILE:-/tmp/weather_openmeteo_gfs_cycle.lock}"
CAMS_ECPDS_SCHEDULE_LOCK="${WEATHER_OPENMETEO_CAMS_FTP_SCHEDULE_LOCK_FILE:-/tmp/weather_openmeteo_cams_ftp_schedule.lock}"
CAMS_ECPDS_CYCLE_LOCK="${WEATHER_OPENMETEO_CAMS_FTP_LOCK_FILE:-/tmp/weather_openmeteo_cams_ftp_cycle.lock}"
CAMS_ADS_SCHEDULE_LOCK="${WEATHER_OPENMETEO_CAMS_ADS_SCHEDULE_LOCK_FILE:-/tmp/weather_openmeteo_cams_ads_schedule.lock}"
SOURCE_REMOTE="${WEATHER_RUST_SOURCE_REMOTE:-origin}"
SOURCE_BRANCH="${WEATHER_RUST_SOURCE_BRANCH:-main}"
EXPECTED_REMOTE_URL="${WEATHER_RUST_EXPECTED_REMOTE_URL:-https://github.com/89199156-design/weather_forecast_server.git}"
SUDO_BIN="${WEATHER_SUDO_BIN:-sudo}"
SYSTEMCTL_BIN="${WEATHER_SYSTEMCTL_BIN:-systemctl}"
CURL_BIN="${WEATHER_CURL_BIN:-curl}"

API_ARTIFACTS=(om-api om-raw-point)
WEBP_ARTIFACTS=(om-webp om-grid-verify om-webp-api-verify om-webp-inspect)
ALL_ARTIFACTS=("${API_ARTIFACTS[@]}" "${WEBP_ARTIFACTS[@]}")

declare -A EXPECTED_SHA=()
declare -a LINK_PATHS=()
declare -a LINK_TARGETS=()
declare -a OLD_LINK_TARGETS=()
declare -a RELEASE_TMP_PATHS=()
rollback_required=false

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "$command_name is required" >&2
    exit 1
  fi
}

prepare_deployment_gate_lock() {
  local lock_file="$1"
  local lock_dir
  lock_dir="$(dirname "$lock_file")"
  mkdir -p "$lock_dir"
  if [[ -L "$lock_file" ]]; then
    echo "refusing symlink task lock: $lock_file" >&2
    return 1
  fi
  "$SUDO_BIN" -n touch "$lock_file"
  if [[ ! -f "$lock_file" || -L "$lock_file" ]]; then
    echo "task lock is not a regular file: $lock_file" >&2
    return 1
  fi
  "$SUDO_BIN" -n chgrp "$DEPLOY_LOCK_GROUP" "$lock_file"
  "$SUDO_BIN" -n chmod 0660 "$lock_file"
}

prepare_deployment_gate_locks() {
  local lock_file
  for lock_file in \
    "$GFS_SCHEDULE_LOCK" \
    "$GFS_CYCLE_LOCK" \
    "$CAMS_ECPDS_SCHEDULE_LOCK" \
    "$CAMS_ECPDS_CYCLE_LOCK" \
    "$CAMS_ADS_SCHEDULE_LOCK"; do
    prepare_deployment_gate_lock "$lock_file"
  done
}

sudo_systemctl() {
  "$SUDO_BIN" -n "$SYSTEMCTL_BIN" "$@"
}

cleanup_release_temps() {
  local path base
  for path in "${RELEASE_TMP_PATHS[@]}"; do
    base="$(basename "$path")"
    if [[ -d "$path" && "$base" == ".${release_id}.tmp."* ]]; then
      rm -rf -- "$path"
    fi
  done
}

expected_sha() {
  local artifact="$1"
  if [[ -z "${EXPECTED_SHA[$artifact]:-}" ]]; then
    echo "build manifest is missing artifact: $artifact" >&2
    return 1
  fi
  printf '%s\n' "${EXPECTED_SHA[$artifact]}"
}

verify_artifact() {
  local path="$1"
  local artifact="$2"
  local actual expected
  if [[ ! -f "$path" || ! -x "$path" ]]; then
    echo "missing executable artifact: $path" >&2
    return 1
  fi
  actual="$(sha256sum "$path" | awk '{print $1}')"
  expected="$(expected_sha "$artifact")"
  if [[ "$actual" != "$expected" ]]; then
    echo "artifact checksum mismatch: $path" >&2
    echo "expected=$expected actual=$actual" >&2
    return 1
  fi
}

verify_release() {
  local release="$1"
  shift
  local artifact manifest_actual
  if [[ ! -d "$release" || -L "$release" ]]; then
    echo "release is not an immutable directory: $release" >&2
    return 1
  fi
  if [[ ! -f "$release/build.json" ]]; then
    echo "release build manifest is missing: $release/build.json" >&2
    return 1
  fi
  manifest_actual="$(sha256sum "$release/build.json" | awk '{print $1}')"
  if [[ "$manifest_actual" != "$manifest_sha256" ]]; then
    echo "release build manifest differs from the requested build: $release" >&2
    return 1
  fi
  for artifact in "$@"; do
    verify_artifact "$release/$artifact" "$artifact"
  done
}

prepare_release() {
  local root="$1"
  local final="$2"
  shift 2
  local artifact tmp
  install -d -m 0755 "$root/bin" "$root/releases"
  if [[ -e "$final" || -L "$final" ]]; then
    verify_release "$final" "$@"
    return
  fi

  tmp="$(mktemp -d "$root/releases/.${release_id}.tmp.XXXXXX")"
  RELEASE_TMP_PATHS+=("$tmp")
  for artifact in "$@"; do
    install -m 0755 "$BUILD_ROOT/$artifact" "$tmp/$artifact"
  done
  install -m 0644 "$BUILD_ROOT/build.json" "$tmp/build.json"
  verify_release "$tmp" "$@"
  mv -T "$tmp" "$final"
}

atomic_link() {
  local target="$1"
  local link="$2"
  local tmp="${link}.next.$$"
  rm -f -- "$tmp"
  ln -s "$target" "$tmp"
  mv -Tf "$tmp" "$link"
}

capture_links() {
  local link
  OLD_LINK_TARGETS=()
  for link in "${LINK_PATHS[@]}"; do
    if [[ -L "$link" ]]; then
      OLD_LINK_TARGETS+=("$(readlink "$link")")
    elif [[ -e "$link" ]]; then
      echo "refusing to replace non-symlink executable: $link" >&2
      return 1
    else
      OLD_LINK_TARGETS+=("")
    fi
  done
}

restore_links() {
  local index link old_target
  for index in "${!LINK_PATHS[@]}"; do
    link="${LINK_PATHS[$index]}"
    old_target="${OLD_LINK_TARGETS[$index]}"
    if [[ -n "$old_target" ]]; then
      atomic_link "$old_target" "$link"
    else
      rm -f -- "$link"
    fi
  done
}

restore_previous_release() {
  set +e
  if [[ "$rollback_required" == true ]]; then
    echo "deployment failed; restoring previous Rust executable links" >&2
    restore_links
    sudo_systemctl restart "$API_SERVICE"
    sudo_systemctl is-active --quiet "$API_SERVICE"
  fi
}

rollback() {
  local rc=$?
  trap - ERR HUP INT TERM
  restore_previous_release
  exit "$rc"
}

rollback_signal() {
  local signal="$1"
  local rc=1
  trap - ERR HUP INT TERM
  case "$signal" in
    HUP) rc=129 ;;
    INT) rc=130 ;;
    TERM) rc=143 ;;
  esac
  restore_previous_release
  exit "$rc"
}

for command_name in bash git python3 sha256sum install flock mktemp readlink "$SUDO_BIN" "$SYSTEMCTL_BIN" "$CURL_BIN"; do
  require_command "$command_name"
done

cd "$APP_ROOT"
bash "$SCRIPT_DIR/build_native_rust_artifacts.sh" "$BUILD_ROOT"

status="$(git status --porcelain --untracked-files=all)"
if [[ -n "$status" ]]; then
  echo "refusing to deploy from a dirty worktree" >&2
  printf '%s\n' "$status" >&2
  exit 1
fi
branch="$(git symbolic-ref --quiet --short HEAD || true)"
if [[ "$branch" != "$SOURCE_BRANCH" ]]; then
  echo "refusing to deploy from branch '$branch'; expected '$SOURCE_BRANCH'" >&2
  exit 1
fi
remote_url="$(git remote get-url "$SOURCE_REMOTE")"
if [[ "$remote_url" != "$EXPECTED_REMOTE_URL" ]]; then
  echo "refusing to deploy from unexpected remote URL: $remote_url" >&2
  exit 1
fi
revision="$(git rev-parse HEAD)"
remote_revision="$(git rev-parse "refs/remotes/$SOURCE_REMOTE/$SOURCE_BRANCH")"
if [[ "$revision" != "$remote_revision" ]]; then
  echo "refusing to deploy: HEAD does not equal $SOURCE_REMOTE/$SOURCE_BRANCH" >&2
  exit 1
fi

python3 - "$BUILD_ROOT/build.json" "$revision" "$SOURCE_REMOTE" "$SOURCE_BRANCH" "$remote_url" "${ALL_ARTIFACTS[@]}" <<'PY'
import json
import re
import sys

path, revision, source_remote, source_branch, remote_url, *required = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    manifest = json.load(handle)
expected = {
    "repository": "89199156-design/weather_forecast_server",
    "revision": revision,
    "source_remote": source_remote,
    "source_branch": source_branch,
    "remote_url": remote_url,
}
for key, value in expected.items():
    if manifest.get(key) != value:
        raise SystemExit(f"build manifest {key} does not match checked-out source")
artifacts = manifest.get("artifacts", {})
if set(artifacts) != set(required):
    raise SystemExit("build manifest artifact inventory is not exact")
for name in required:
    digest = artifacts[name].get("sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SystemExit(f"invalid artifact checksum for {name}")
PY

while read -r artifact digest; do
  EXPECTED_SHA["$artifact"]="$digest"
done < <(
  python3 - "$BUILD_ROOT/build.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
for name, metadata in sorted(manifest["artifacts"].items()):
    print(name, metadata["sha256"])
PY
)

for artifact in "${ALL_ARTIFACTS[@]}"; do
  verify_artifact "$BUILD_ROOT/$artifact" "$artifact"
done

manifest_sha256="$(sha256sum "$BUILD_ROOT/build.json" | awk '{print $1}')"
release_id="${revision}-${manifest_sha256:0:16}"
api_release="$API_ROOT/releases/$release_id"
webp_release="$WEBP_ROOT/releases/$release_id"
prepare_deployment_gate_locks
trap cleanup_release_temps EXIT

{
  flock -n 9 || {
    echo "GFS schedule task is active; refusing concurrent Rust deployment" >&2
    exit 1
  }
  flock -n 8 || {
    echo "GFS production cycle is active; refusing concurrent Rust deployment" >&2
    exit 1
  }
  flock -n 7 || {
    echo "CAMS ECPDS schedule task is active; refusing concurrent Rust deployment" >&2
    exit 1
  }
  flock -n 6 || {
    echo "CAMS ECPDS production cycle is active; refusing concurrent Rust deployment" >&2
    exit 1
  }
  flock -n 5 || {
    echo "CAMS ADS task is active; refusing concurrent Rust deployment" >&2
    exit 1
  }

  prepare_release "$API_ROOT" "$api_release" "${API_ARTIFACTS[@]}"
  prepare_release "$WEBP_ROOT" "$webp_release" "${WEBP_ARTIFACTS[@]}"

  LINK_PATHS=(
    "$API_ROOT/bin/om-api"
    "$API_ROOT/bin/om-raw-point"
    "$WEBP_ROOT/bin/om-webp"
    "$WEBP_ROOT/bin/om-grid-verify"
    "$WEBP_ROOT/bin/om-webp-api-verify"
    "$WEBP_ROOT/bin/om-webp-inspect"
  )
  LINK_TARGETS=(
    "$api_release/om-api"
    "$api_release/om-raw-point"
    "$webp_release/om-webp"
    "$webp_release/om-grid-verify"
    "$webp_release/om-webp-api-verify"
    "$webp_release/om-webp-inspect"
  )
  capture_links
  rollback_required=true
  trap rollback ERR
  trap 'rollback_signal HUP' HUP
  trap 'rollback_signal INT' INT
  trap 'rollback_signal TERM' TERM

  for index in "${!LINK_PATHS[@]}"; do
    atomic_link "${LINK_TARGETS[$index]}" "${LINK_PATHS[$index]}"
  done

  WEATHER_OM_API_HEALTHCHECK_URL="$API_HEALTHCHECK_URL" \
    bash "$SCRIPT_DIR/install_native_api_dem_root.sh"
  sudo_systemctl restart "$API_SERVICE"
  sudo_systemctl is-active --quiet "$API_SERVICE"
  "$CURL_BIN" --fail --silent --show-error \
    --connect-timeout 2 --max-time 30 --retry 5 --retry-delay 1 --retry-connrefused \
    "$API_HEALTHCHECK_URL" >/dev/null

  main_pid="$(sudo_systemctl show "$API_SERVICE" --property=MainPID --value)"
  if [[ ! "$main_pid" =~ ^[1-9][0-9]*$ ]]; then
    echo "systemd returned an invalid MainPID for $API_SERVICE: $main_pid" >&2
    false
  fi
  running_api_sha256="$(sha256sum "/proc/$main_pid/exe" | awk '{print $1}')"
  if [[ "$running_api_sha256" != "$(expected_sha om-api)" ]]; then
    echo "running API executable checksum does not match the deployed artifact" >&2
    false
  fi

  for index in "${!LINK_PATHS[@]}"; do
    artifact="$(basename "${LINK_PATHS[$index]}")"
    if [[ "$(readlink -f "${LINK_PATHS[$index]}")" != "$(readlink -f "${LINK_TARGETS[$index]}")" ]]; then
      echo "installed executable link does not select the intended release: ${LINK_PATHS[$index]}" >&2
      false
    fi
    verify_artifact "${LINK_PATHS[$index]}" "$artifact"
  done

  rollback_required=false
  trap - ERR HUP INT TERM
} 9>"$GFS_SCHEDULE_LOCK" \
  8>"$GFS_CYCLE_LOCK" \
  7>"$CAMS_ECPDS_SCHEDULE_LOCK" \
  6>"$CAMS_ECPDS_CYCLE_LOCK" \
  5>"$CAMS_ADS_SCHEDULE_LOCK"

echo "revision=$revision"
echo "release_id=$release_id"
echo "api=$api_release/om-api sha256=${EXPECTED_SHA[om-api]} running_pid=$main_pid"
echo "raw_point=$api_release/om-raw-point sha256=${EXPECTED_SHA[om-raw-point]}"
for artifact in "${WEBP_ARTIFACTS[@]}"; do
  echo "$artifact=$webp_release/$artifact sha256=${EXPECTED_SHA[$artifact]}"
done
