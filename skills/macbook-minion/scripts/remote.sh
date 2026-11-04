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

active_job() {
  local job
  if [ -e "$root/recovery-in-progress" ] || [ -L "$root/recovery-in-progress" ]; then
    job=
    if [ ! -L "$root/recovery-in-progress" ] && [ ! -L "$root/recovery-in-progress/job" ] && [ -f "$root/recovery-in-progress/job" ]; then
      job=$(head -c 81 "$root/recovery-in-progress/job" 2>/dev/null || true)
    fi
    if [[ "$job" =~ ^j-[a-z0-9-]{1,77}$ ]]; then printf '%s\n' "$job"; else printf 'pending-recovery\n'; fi
  else
    cat "$root/active/job" 2>/dev/null || true
  fi
}
boot_epoch() { sysctl -n kern.boottime | awk -F 'sec = |,' '{print $2}'; }
utc_epoch() { date -j -u -f '%Y-%m-%dT%H:%M:%SZ' "$1" '+%s' 2>/dev/null; }
modified_epoch() { stat -f '%m' "$1"; }
lock_inspect() {
  local id=$1 dir boot finished finished_epoch field eligible=no evidence=yes
  valid_id "$id"; dir="$root/jobs/$id"; safe_dir "$dir"
  for field in owner finished_at exit_code retain_lock worker_events.tsv; do
    if [ -L "$dir/$field" ] || [ ! -f "$dir/$field" ]; then evidence=no; fi
  done
  [ "$evidence" = yes ] || fail 'Unsafe or missing job recovery evidence'
  boot=$(boot_epoch || true)
  finished=$(cat "$dir/finished_at" 2>/dev/null || true)
  finished_epoch=$(utc_epoch "$finished" || true)
  if [[ "$boot" =~ ^[0-9]+$ && "$finished_epoch" =~ ^[0-9]+$ ]] &&
      [ "$boot" -gt "$finished_epoch" ] && [ -f "$dir/exit_code" ] &&
      [ -f "$dir/retain_lock" ]; then
    eligible=yes
  fi
  printf 'job=%s\nowner=%s\nfinished_at=%s\nboot_epoch=%s\nfinished_epoch=%s\nreboot_proves_processes_ended=%s\n' \
    "$id" "$(cat "$dir/owner" 2>/dev/null || true)" "$finished" "$boot" "$finished_epoch" "$eligible"
}

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
  printf 'active_job=%s\n' "$(active_job)"
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
  printf 'active_job=%s\n' "$(active_job)"
}
# Set the caller's local scalar without forking cat/a command substitution for
# each field in a full-history overview. Match command substitution's removal
# of trailing newlines while preserving whitespace and embedded newlines.
read_status_scalar() {
  local chunk
  scalar=
  if [ -f "$1" ] && [ -r "$1" ]; then
    # Bash command substitution discarded NUL bytes, not the bytes after them.
    # Consume every segment so malformed saved PIDs cannot become valid prefixes.
    while IFS= read -r -d '' chunk || [ -n "$chunk" ]; do
      scalar=$scalar$chunk
    done < "$1"
    while [[ "$scalar" == *$'\n' ]]; do scalar=${scalar%$'\n'}; done
  else
    scalar=${2-}
  fi
}
job_status() {
  local id=$1 dir pid saved live scalar encoded
  valid_id "$id"; dir="$root/jobs/$id"; safe_dir "$dir"
  printf 'job=%s\n' "$id"
  read_status_scalar "$dir/owner" unknown
  printf 'owner=%s\n' "$scalar"
  local field
  for field in snapshot created_at started_at finished_at; do
    read_status_scalar "$dir/$field"
    printf '%s=%s\n' "$field" "$scalar"
  done
  if [ -e "$dir/launched" ]; then printf 'launched=yes\n'; fi
  for field in metadata.json worker_events.tsv; do
    if [ -f "$dir/$field" ]; then
      encoded=$(base64 < "$dir/$field")
      printf '%s_b64=%s\n' "${field%%.*}" "${encoded//$'\n'/}"
    fi
  done
  if [ -f "$dir/retain_lock" ] && [ ! -f "$dir/lock_recovered" ]; then printf 'lock_retained=yes\n'; fi
  if [ -f "$dir/lock_recovered" ]; then printf 'lock_recovered=yes\n'; fi
  if [ -f "$dir/exit_code" ]; then
    read_status_scalar "$dir/exit_code"
    printf 'state=finished\nexit_code=%s\n' "$scalar"
  elif [ -f "$dir/pid" ] && [ -f "$dir/process_start" ]; then
    read_status_scalar "$dir/pid"; pid=$scalar
    read_status_scalar "$dir/process_start"; saved=$scalar
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
    lock_inspect) lock_inspect "$1" ;;
    recover_lock)
      id=$1; valid_id "$id"; dir="$root/jobs/$id"; safe_dir "$dir"
      local field guard boot guard_epoch guard_job
      guard="$root/recovery-in-progress"
      for field in owner finished_at exit_code retain_lock worker_events.tsv; do
        [ ! -L "$dir/$field" ] && [ -f "$dir/$field" ] || fail 'Unsafe or missing job recovery evidence'
      done
      [ "$(cat "$dir/owner")" = "$2" ] || fail 'Job owner changed; recovery refused'
      [[ "$3" =~ ^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$ ]] || fail 'Invalid recovery owner'
      [ "$(lock_inspect "$id" | sed -n 's/^reboot_proves_processes_ended=//p')" = yes ] || fail 'A finished retained job and a later reboot must be verified'
      for field in recovered-lock lock_recovered lock_recovery_intent recovered-recovery-guard; do
        [ ! -L "$dir/$field" ] || fail 'Unsafe recovery artifact'
      done
      for field in lock_recovered lock_recovery_intent; do
        [ ! -e "$dir/$field" ] || [ -f "$dir/$field" ] || fail 'Unsafe recovery record'
      done
      boot=$(boot_epoch)
      [[ "$boot" =~ ^[0-9]+$ ]] || fail 'Cannot verify worker boot'
      if [ -e "$guard" ] || [ -L "$guard" ]; then
        safe_dir "$guard"
        # An interrupted recovery remains a reservation barrier. Resume only
        # after another boot proves the earlier repair process cannot continue.
        guard_epoch=$(modified_epoch "$guard")
        [[ "$boot" =~ ^[0-9]+$ && "$guard_epoch" =~ ^[0-9]+$ ]] &&
          [ "$boot" -gt "$guard_epoch" ] || fail 'Recovery interrupted; inspect and reboot before resuming this job'
        [ ! -L "$guard/job" ] || fail 'Unsafe recovery guard'
        [ ! -e "$guard/job" ] || [ -f "$guard/job" ] || fail 'Unsafe recovery guard'
        guard_job=$(cat "$guard/job" 2>/dev/null || true)
        [ -z "$guard_job" ] || [ "$guard_job" = "$id" ] || fail 'Different job owns recovery'
        if [ -z "$guard_job" ]; then
          if [ -e "$dir/recovered-lock" ]; then
            safe_dir "$dir/recovered-lock"
            [ ! -L "$dir/recovered-lock/job" ] && [ "$(cat "$dir/recovered-lock/job")" = "$id" ] || fail 'Cannot identify interrupted recovery'
          else
            safe_dir "$root/active"
            [ ! -L "$root/active/job" ] && [ "$(cat "$root/active/job")" = "$id" ] || fail 'Cannot identify interrupted recovery'
          fi
        fi
        [ ! -e "$dir/recovered-recovery-guard" ] || fail 'Recovery guard archive exists'
      else
        [ ! -e "$dir/recovered-lock" ] && [ ! -e "$dir/lock_recovered" ] || fail 'Job already recovered; inspect status'
        [ ! -e "$dir/recovered-recovery-guard" ] || fail 'Recovery guard archive exists'
        safe_dir "$root/active"
        [ ! -L "$root/active/job" ] && [ "$(cat "$root/active/job")" = "$id" ] || fail 'Job does not own active lock'
        [ "$(ls -A "$root/active")" = job ] || fail 'Unexpected lock contents; recovery refused'
        mkdir "$guard" || fail 'Another recovery is in progress'
      fi
      # One repair attempt per worker boot, including concurrent resume calls.
      mkdir "$guard/claim-$boot" || fail 'Recovery already claimed during this boot'
      printf '%s\n' "$id" > "$guard/job"
      printf '%s\t%s\t%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$id" "$3" >> "$dir/lock_recovery_intent"
      sync
      if [ -e "$dir/recovered-lock" ]; then
        safe_dir "$dir/recovered-lock"
        [ ! -L "$dir/recovered-lock/job" ] && [ "$(cat "$dir/recovered-lock/job")" = "$id" ] || fail 'Archived lock differs'
        [ ! -e "$root/active" ] && [ ! -L "$root/active" ] || fail 'Another active lock must be inspected'
      else
        safe_dir "$root/active"
        [ ! -L "$root/active/job" ] && [ "$(cat "$root/active/job")" = "$id" ] || fail 'Active lock changed'
        [ "$(ls -A "$root/active")" = job ] || fail 'Unexpected lock contents'
        mv "$root/active" "$dir/recovered-lock"
      fi
      printf '%s\n%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$3" >> "$dir/lock_recovered"
      printf '%s\tlock_recovered\t\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$dir/worker_events.tsv"
      sync
      mv "$guard" "$dir/recovered-recovery-guard"
      sync
      printf 'job=%s\nlock_recovered=yes\nartifacts=preserved\n' "$id" ;;
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
      [ ! -e "$root/recovery-in-progress" ] && [ ! -L "$root/recovery-in-progress" ] || fail 'Lock recovery must finish before dispatch'
      mkdir "$root/active" || fail 'Another job is active, or an interrupted job needs review'
      if [ -e "$root/recovery-in-progress" ] || [ -L "$root/recovery-in-progress" ]; then
        rmdir "$root/active"
        fail 'Lock recovery started before reservation completed'
      fi
      printf '%s\n' "$id" > "$root/active/job"
      mkdir "$root/jobs/$id"
      printf '%s\n' "${4:-unknown}" > "$root/jobs/$id/owner"
      printf '%s\n' "$snapshot" > "$root/jobs/$id/snapshot"
      date -u '+%Y-%m-%dT%H:%M:%SZ' > "$root/jobs/$id/created_at"
      printf '%s\taccepted\t\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$root/jobs/$id/worker_events.tsv"
      mkdir "$root/jobs/$id/output"
      printf 'job=%s\n' "$id" ;;
    verify_job)
      [ ! -e "$root/recovery-in-progress" ] && [ ! -L "$root/recovery-in-progress" ] || fail 'Lock recovery must finish before dispatch'
      id=$1; valid_id "$id"; dir="$root/jobs/$id"; safe_dir "$dir"
      [ "$(cat "$root/active/job")" = "$id" ] || fail 'Job does not own active lock'
      [ ! -e "$dir/launched" ] || fail 'Cannot restage a launched job'
      (cd "$dir" && shasum -a 256 -c payload.sha256 >/dev/null)
      touch "$dir/payload.ready"
      printf 'verified=%s\n' "$id" ;;
    launch)
      [ ! -e "$root/recovery-in-progress" ] && [ ! -L "$root/recovery-in-progress" ] || fail 'Lock recovery must finish before dispatch'
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
      printf 'active_job=%s\n' "$(active_job)"
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
