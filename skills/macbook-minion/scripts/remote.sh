#!/bin/bash
# Sent over SSH on demand. Compatible with the system Bash 3.2 on older Macs.
set -euo pipefail
umask 077
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# Newer SSH clients can forward C.UTF-8, which older macOS does not provide.
export LC_ALL=C LANG=C LC_CTYPE=C

fail() { printf '%s\n' "$*" >&2; exit 1; }
valid_id() { [[ "$1" =~ ^[a-z0-9][a-z0-9-]{0,79}$ ]] || fail 'Invalid ID'; }
safe_dir() { [ ! -L "$1" ] && [ -d "$1" ] || fail "Unsafe or missing directory: $1"; }
layout() {
  safe_dir "$root"
  safe_dir "$root/snapshots"
  safe_dir "$root/jobs"
}
free_kib() { df -Pk "$1" | awk 'END {print $4}'; }
space_check() {
  local free needed
  free=$(free_kib "$root")
  needed=$1
  [[ "$free" =~ ^[0-9]+$ && "$needed" =~ ^[0-9]+$ ]] || fail 'Cannot measure free space'
  [ "$free" -ge "$needed" ] || fail "Insufficient space: ${free} KiB free; ${needed} KiB required"
}
# BEGIN WORKER IDENTITY
minion_worker_identity() {
  local raw digest
  raw=$(sysctl -n kern.hostuuid 2>/dev/null || true)
  if [ -z "$raw" ]; then
    raw=$(/usr/sbin/ioreg -rd1 -c IOPlatformExpertDevice 2>/dev/null | /usr/bin/awk -F '"' '$2 == "IOPlatformUUID" {print $4; exit}' || true)
  fi
  [ -n "$raw" ] || return 1
  digest=$(printf '%s' "$raw" | /usr/bin/shasum -a 256) || return 1
  printf '%s\n' "${digest%% *}"
}
# END WORKER IDENTITY

probe() {
  printf 'os=%s\n' "$(uname -s)"
  printf 'arch=%s\n' "$(uname -m)"
  printf 'macos=%s\n' "$(sw_vers -productVersion 2>/dev/null || true)"
  printf 'model=%s\n' "$(sysctl -n hw.model 2>/dev/null || true)"
  printf 'cpu=%s\n' "$(sysctl -n machdep.cpu.brand_string 2>/dev/null || true)"
  printf 'logical_cpus=%s\n' "$(sysctl -n hw.logicalcpu 2>/dev/null || true)"
  printf 'memory_bytes=%s\n' "$(sysctl -n hw.memsize 2>/dev/null || true)"
  printf 'load1=%s\n' "$(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}' || true)"
  local pressure uid
  pressure=$(sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null || true)
  case "$pressure" in
    1) pressure=normal ;;
    2) pressure=warn ;;
    4) pressure=critical ;;
    *) pressure=unknown ;;
  esac
  printf 'memory_pressure=%s\n' "$pressure"
  uid=$(minion_worker_identity || true)
  if [ -n "$uid" ]; then
    printf 'worker_uid=%s\n' "$uid"
  fi
  printf 'active_job=%s\n' "$(cat "$root/active/job" 2>/dev/null || true)"
  printf 'free_kib='; free_kib "$HOME"
  printf 'power='; pmset -g batt 2>/dev/null | head -n 1 || true
  local tool
  for tool in bash rsync shasum caffeinate git python3 node ruby npm ffmpeg xcodebuild; do
    printf 'tool_%s=%s\n' "$tool" "$(command -v "$tool" || true)"
  done
  for tool in python3 node ruby; do
    if command -v "$tool" >/dev/null 2>&1; then
      printf 'version_%s=%s\n' "$tool" "$("$tool" --version 2>/dev/null | head -n 1 || true)"
    fi
  done
}
browser_probe() {
  printf 'arch='; uname -m
  printf 'python3=%s\n' "$(command -v python3 || true)"
  printf 'chrome_version='
  /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' '/Applications/Google Chrome.app/Contents/Info.plist' 2>/dev/null || printf '\n'
  if [ -f "$root/browser/driver.json" ]; then
    printf 'driver_installed=yes\n'
  else
    printf 'driver_installed=no\n'
  fi
  printf 'active_job=%s\n' "$(cat "$root/active/job" 2>/dev/null || true)"
}
job_status() {
  local id=$1 dir pid saved live
  valid_id "$id"; dir="$root/jobs/$id"; safe_dir "$dir"
  printf 'job=%s\n' "$id"
  printf 'owner=%s\n' "$(cat "$dir/owner" 2>/dev/null || printf unknown)"
  local field
  for field in snapshot created_at started_at finished_at; do
    printf '%s=%s\n' "$field" "$(cat "$dir/$field" 2>/dev/null || true)"
  done
  if [ -e "$dir/launched" ]; then printf 'launched=yes\n'; fi
  for field in metadata.json worker_events.tsv; do
    if [ -f "$dir/$field" ]; then
      printf '%s_b64=' "${field%%.*}"
      base64 < "$dir/$field" | tr -d '\n'; printf '\n'
    fi
  done
  if [ -f "$dir/retain_lock" ]; then printf 'lock_retained=yes\n'; fi
  if [ -f "$dir/exit_code" ]; then
    printf 'state=finished\nexit_code=%s\n' "$(cat "$dir/exit_code")"
  elif [ -f "$dir/pid" ] && [ -f "$dir/process_start" ]; then
    pid=$(cat "$dir/pid"); saved=$(cat "$dir/process_start")
    [[ "$pid" =~ ^[0-9]+$ ]] || fail 'Invalid saved PID'
    live=$(ps -p "$pid" -o lstart= 2>/dev/null || true)
    if [ -n "$live" ] && [ "$live" = "$saved" ]; then
      printf 'state=running\npid=%s\n' "$pid"
    else
      printf 'state=interrupted\n'
    fi
  else
    printf 'state=staged_or_interrupted\n'
  fi
}
remote_main() {
  root=$1; local action=$2; shift 2
  case "$action" in
    probe) probe; return ;;
    browser_probe) browser_probe; return ;;
    init)
      [ ! -L "$root" ] || fail 'Workspace root is a symlink'
      mkdir -p "$root"
      safe_dir "$root"
      local part
      for part in snapshots jobs; do
        [ ! -L "$root/$part" ] || fail 'Workspace component is a symlink'
        mkdir -p "$root/$part"
      done
      printf 'workspace=%s\n' "$root"; return ;;
  esac
  layout
  local id dir snapshot
  case "$action" in
    space) printf 'free_kib=%s\n' "$(free_kib "$root")" ;;
    prepare_snapshot)
      id=$1; valid_id "$id"; space_check "$2"
      mkdir "$root/snapshots/$id"
      printf 'snapshot=%s\n' "$id" ;;
    verify_snapshot)
      id=$1; valid_id "$id"; safe_dir "$root/snapshots/$id"
      (cd "$root/snapshots/$id" && shasum -a 256 -c "$root/snapshots/$id.sha256" >/dev/null)
      touch "$root/snapshots/$id.ready"
      printf 'verified=%s\n' "$id" ;;
    prepare_job)
      id=$1; snapshot=$2; valid_id "$id"; valid_id "$snapshot"
      safe_dir "$root/snapshots/$snapshot"
      [ -f "$root/snapshots/$snapshot.ready" ] || fail 'Snapshot is not verified'
      space_check "$3"
      mkdir "$root/active" || fail 'Another job is active, or an interrupted job needs review'
      printf '%s\n' "$id" > "$root/active/job"
      mkdir "$root/jobs/$id"
      printf '%s\n' "${4:-unknown}" > "$root/jobs/$id/owner"
      printf '%s\n' "$snapshot" > "$root/jobs/$id/snapshot"
      date -u '+%Y-%m-%dT%H:%M:%SZ' > "$root/jobs/$id/created_at"
      printf '%s\taccepted\t\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$root/jobs/$id/worker_events.tsv"
      mkdir "$root/jobs/$id/output"
      printf 'job=%s\n' "$id" ;;
    verify_job)
      id=$1; valid_id "$id"; dir="$root/jobs/$id"; safe_dir "$dir"
      [ "$(cat "$root/active/job")" = "$id" ] || fail 'Job does not own active lock'
      [ ! -e "$dir/launched" ] || fail 'Cannot restage a launched job'
      (cd "$dir" && shasum -a 256 -c payload.sha256 >/dev/null)
      touch "$dir/payload.ready"
      printf 'verified=%s\n' "$id" ;;
    launch)
      id=$1; valid_id "$id"; dir="$root/jobs/$id"; safe_dir "$dir"
      [ "$(cat "$root/active/job")" = "$id" ] || fail 'Job does not own active lock'
      [ ! -e "$dir/launched" ] || fail 'Job already launched; inspect status instead of retrying'
      if [ -f "$dir/metadata.json" ]; then
        [ -f "$dir/payload.ready" ] || fail 'Job payload is not verified'
      fi
      # mkdir is the atomic claim: concurrent retries must never launch twice.
      mkdir "$dir/launched"
      printf '%s\tlaunched\t\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$dir/worker_events.tsv"
      nohup /bin/bash "$dir/runner.sh" "$root" "$id" </dev/null >"$dir/log.txt" 2>&1 &
      printf 'job=%s\nstate=submitted\n' "$id" ;;
    status) job_status "$1" ;;
    logs)
      id=$1; valid_id "$id"; safe_dir "$root/jobs/$id"
      tail -n 120 "$root/jobs/$id/log.txt" ;;
    list)
      for dir in "$root"/jobs/*; do
        [ -d "$dir" ] || continue
        job_status "${dir##*/}"
        printf '\n'
      done ;;
    overview)
      printf 'active_job=%s\n' "$(cat "$root/active/job" 2>/dev/null || true)"
      printf 'worker_at=%s\n\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
      for dir in "$root"/jobs/*; do
        [ -d "$dir" ] || continue
        job_status "${dir##*/}"; printf '\n'
      done ;;
    manifest)
      # Freeze outputs by allowing only finished jobs; snapshot reads need no job.
      local kind=$1
      id=$2; valid_id "$id"
      case "$kind" in
        jobs) [ -f "$root/jobs/$id/exit_code" ] || fail 'Job has not finished' ;;
        snapshots) [ ! -e "$root/active" ] || fail 'Wait for the active job before retrieving a snapshot' ;;
        *) fail 'Invalid artifact kind' ;;
      esac
      dir="$root/$kind/$id"; safe_dir "$dir"
      # shasum escapes unusual filenames; controller rejects those on verification.
      (cd "$dir" && find . -type f -exec shasum -a 256 {} +)
      ;;
    *) fail "Unknown remote action: $action" ;;
  esac
}
