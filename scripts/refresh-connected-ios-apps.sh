#!/bin/bash

# Refresh already-installed personal iOS apps from local device builds.
#
# This script deliberately does not install an app merely because a local
# build exists. It first discovers the connected device's installed bundle
# identifiers, then installs only matching iphoneos .app bundles found in
# Xcode DerivedData.

set -u -o pipefail

PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin:/Applications/Xcode.app/Contents/Developer/usr/bin"
export PATH

LOG_DIR="${AGENT_SLACK_REFRESH_LOG_DIR:-${HOME}/Library/Logs/AgentSlack}"
DERIVED_DATA_ROOT="${AGENT_SLACK_DERIVED_DATA_ROOT:-${HOME}/Library/Developer/Xcode/DerivedData}"
DEVICE_IDS_OVERRIDE="${AGENT_SLACK_IOS_DEVICE_IDS:-}"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/ios-refresh.log"
exec >>"$LOG_FILE" 2>&1

timestamp() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*"
}

if ! command -v xcrun >/dev/null 2>&1 || ! command -v xcodebuild >/dev/null 2>&1; then
  log 'Xcode command-line tools are unavailable; skipping refresh.'
  exit 0
fi

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/agent-slack-ios-refresh.XXXXXX")"
cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

discover_devices() {
  if [ -n "$DEVICE_IDS_OVERRIDE" ]; then
    printf '%s\n' "$DEVICE_IDS_OVERRIDE" | tr ',' '\n' | sed '/^[[:space:]]*$/d'
    return 0
  fi

  local device_json="$TMP_ROOT/devices.json"
  if ! xcrun devicectl list devices --json-output "$device_json" --timeout 30 >/dev/null 2>&1; then
    log 'Could not query CoreDevice for connected devices; skipping refresh.'
    return 0
  fi
  if [ ! -s "$device_json" ]; then
    return 0
  fi

  /usr/bin/python3 - "$device_json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)

def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)

for item in walk(payload):
    identifier = item.get("identifier") or item.get("udid") or item.get("deviceIdentifier")
    platform = str(item.get("platform") or item.get("platformType") or item.get("devicePlatform") or "").lower()
    name = str(item.get("name") or item.get("deviceName") or "").lower()
    connection = item.get("connectionProperties") or {}
    capabilities = item.get("capabilities") or []
    has_connection_transport = bool(connection.get("transportType"))
    has_connected_tunnel = connection.get("tunnelState") == "connected"
    can_connect = any(
        isinstance(capability, dict)
        and capability.get("featureIdentifier") == "com.apple.coredevice.feature.connectdevice"
        for capability in capabilities
    )
    if not identifier or (platform and "mac" in platform):
        continue
    # CoreDevice lists paired devices as well as connected devices. A
    # transport, connected tunnel, or connect-device capability is the
    # positive signal that this device can be refreshed now.
    if not (has_connection_transport or has_connected_tunnel or can_connect):
        continue
    if platform and not any(token in platform for token in ("ios", "ipad", "iphone")):
        if not any(token in name for token in ("iphone", "ipad")):
            continue
    print(identifier)
PY
}

installed_bundle_ids() {
  local device_id="$1"
  local apps_json="$TMP_ROOT/apps-${device_id}.json"
  if ! xcrun devicectl device info apps \
      --device "$device_id" \
      --json-output "$apps_json" \
      --timeout 45 >/dev/null 2>&1; then
    log "Unable to inspect installed apps on device $device_id; skipping device."
    return 0
  fi
  if [ ! -s "$apps_json" ]; then
    return 0
  fi

  /usr/bin/python3 - "$apps_json" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)

def walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("_", "")
            if normalized in {"bundleidentifier", "bundleid"} and isinstance(child, str):
                if re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", child):
                    print(child)
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)

for _ in walk(payload):
    pass
PY
}

find_local_device_apps() {
  if [ ! -d "$DERIVED_DATA_ROOT" ]; then
    return 0
  fi

  find "$DERIVED_DATA_ROOT" -type d -name '*.app' -path '*/Build/Products/*-iphoneos/*.app' -prune -print 2>/dev/null |
    while IFS= read -r app_path; do
      local bundle_id
      bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app_path/Info.plist" 2>/dev/null || true)"
      [ -n "$bundle_id" ] || continue
      local modified
      modified="$(stat -f '%m' "$app_path" 2>/dev/null || printf '0')"
      printf '%s\t%s\t%s\n' "$modified" "$bundle_id" "$app_path"
    done | sort -nr -k1,1
}

mapfile_compat() {
  # macOS ships Bash 3.2, so avoid arrays and process substitutions here.
  while IFS= read -r line; do
    printf '%s\n' "$line"
  done
}

device_count=0
refreshed_count=0
skipped_count=0

local_apps_file="$TMP_ROOT/local-apps.tsv"
find_local_device_apps >"$local_apps_file"

if [ ! -s "$local_apps_file" ]; then
  log "No local iphoneos .app bundles found under $DERIVED_DATA_ROOT; nothing to refresh."
  exit 0
fi

while IFS= read -r device_id; do
  [ -n "$device_id" ] || continue
  device_count=$((device_count + 1))
  log "Inspecting connected device $device_id."

  installed_file="$TMP_ROOT/installed-${device_id}.txt"
  installed_bundle_ids "$device_id" | sort -u >"$installed_file"
  if [ ! -s "$installed_file" ]; then
    log "No installed app bundle identifiers were returned for $device_id (device is likely paired but not actually reachable right now — asleep, locked off Wi-Fi, or not cabled in)."
    continue
  fi
  log "Found $(wc -l <"$installed_file" | tr -d ' ') installed app bundle identifier(s) on $device_id; device is reachable."

  refreshed_bundles_file="$TMP_ROOT/refreshed-${device_id}.txt"
  : >"$refreshed_bundles_file"

  while IFS=$'\t' read -r _modified bundle_id app_path; do
    [ -n "$bundle_id" ] || continue
    if ! grep -Fxq "$bundle_id" "$installed_file"; then
      continue
    fi
    # Several DerivedData folders can contain the same bundle. Once the
    # newest candidate installs, do not retry older copies of that bundle.
    if grep -Fxq "$bundle_id" "$refreshed_bundles_file"; then
      continue
    fi

    log "Refreshing already-installed $bundle_id on $device_id from $app_path."
    if xcrun devicectl device install app --device "$device_id" "$app_path"; then
      refreshed_count=$((refreshed_count + 1))
      printf '%s\n' "$bundle_id" >>"$refreshed_bundles_file"
    else
      log "Install failed for $bundle_id on $device_id."
    fi
  done <"$local_apps_file"

  if [ ! -s "$refreshed_bundles_file" ]; then
    log "No local build in $DERIVED_DATA_ROOT matched any installed bundle ID on $device_id; nothing to refresh there."
  fi
done <<EOF
$(discover_devices)
EOF

if [ "$device_count" -eq 0 ]; then
  log 'No connected iOS/iPadOS devices found; nothing was installed.'
else
  log "Daily refresh pass finished: devices=$device_count refreshed=$refreshed_count."
fi

exit 0
