#!/bin/bash
# Bash 3.2. Read-only probes; only the requested report file is written.
# Run through the shared queue. No installs, upgrades, network requests or sudo.
set -u

section() { printf '\n[%s]\n' "$1"; }
probe() {
  label=$1
  shift
  printf '%s: ' "$label"
  if command -v "$1" >/dev/null 2>&1; then
    "$@" 2>&1 || printf '(probe unavailable; exit %s)\n' "$?"
  else
    printf 'not found\n'
  fi
}

inventory() {
  printf 'MacBook Minion runtime inventory\n'
  printf 'observed_utc: '
  date -u '+%Y-%m-%dT%H:%M:%SZ'
  printf 'scope: active job environment; no update or security attestation\n'
  section platform
  probe architecture uname -m
  probe operating_system sw_vers
  probe model sysctl -n hw.model
  probe cpu sysctl -n machdep.cpu.brand_string
  probe physical_cores sysctl -n hw.physicalcpu
  probe logical_cores sysctl -n hw.logicalcpu
  probe memory_bytes sysctl -n hw.memsize
  probe cpu_features sysctl -n machdep.cpu.features
  probe cpu_leaf7_features sysctl -n machdep.cpu.leaf7_features
  probe disk_free_kib df -kP .

  section runtime_versions
  for runtime in python3 python3.13 python3.14 python3.15; do
    if command -v "$runtime" >/dev/null 2>&1; then
      printf '%s executable: %s\n' "$runtime" "$(command -v "$runtime")"
      probe "$runtime" "$runtime" -I -B --version
      # Distribution metadata only: do not import torch or initialize devices.
      "$runtime" -I -B -c 'import importlib.metadata as m, ssl
print("openssl:", ssl.OPENSSL_VERSION)
for name in ("pip", "torch", "torchvision", "torchaudio"):
    try: print(name + " distribution:", m.version(name))
    except m.PackageNotFoundError: print(name + " distribution: absent")' 2>&1 \
        || printf 'Python package metadata unavailable\n'
    else
      printf '%s: not found\n' "$runtime"
    fi
  done
  if command -v node >/dev/null 2>&1; then
    probe node env -u NODE_OPTIONS node --version
  else
    printf 'node: not found\n'
  fi
  if command -v ruby >/dev/null 2>&1; then
    probe ruby env -u RUBYOPT ruby --disable-gems --version
  else
    printf 'ruby: not found\n'
  fi
  probe homebrew brew --version
  probe developer_tools xcode-select -p
  if [ -f '/Applications/Google Chrome.app/Contents/Info.plist' ]; then
    probe chrome /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
      '/Applications/Google Chrome.app/Contents/Info.plist'
  else
    printf 'chrome application: not found at standard path\n'
  fi

  section boot_security_observation
  probe sip csrutil status
  probe authenticated_root csrutil authenticated-root status
  probe booted_opencore nvram 4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102:opencore-version
  probe booted_oclp nvram 4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102:OCLP-Version
  if [ -f '/Applications/OpenCore-Patcher.app/Contents/Info.plist' ]; then
    probe oclp_application /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
      '/Applications/OpenCore-Patcher.app/Contents/Info.plist'
  else
    printf 'oclp application: not found at standard path\n'
  fi
  printf 'root_patch_status: inspect OCLP Post Install Volume Patch manually\n'
  printf '\nInventory complete. Missing commands are observations, not a failed update.\n'
}

if [ -n "${MINION_OUTPUT_DIR:-}" ]; then
  # The shared runner creates this output directory; do not create other paths.
  if [ ! -d "$MINION_OUTPUT_DIR" ]; then
    printf 'MINION_OUTPUT_DIR does not exist\n' >&2
    exit 1
  fi
  inventory > "$MINION_OUTPUT_DIR/runtime-inventory.txt"
  printf 'Runtime inventory written to runtime-inventory.txt\n'
else
  inventory
fi
