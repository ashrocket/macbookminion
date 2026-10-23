#!/bin/bash
# Runs locally on the worker Mac; needs no network access and does not
# depend on the controller or the SSH pairing in any way.
#
# Stops any MacBook Minion job still running on THIS Mac right now. It does
# NOT block new connections by itself -- for that, turn off Remote Login in
# System Settings > General > Sharing. This script only guarantees that
# nothing already running keeps running.
set -u
umask 077
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

checked=0
stopped=0

stop_job() {
  local dir="$1" pid saved live job_id cpid deadline forced=0
  [ -f "$dir/exit_code" ] && return 0
  [ -f "$dir/pid" ] && [ -f "$dir/process_start" ] || return 0
  pid=$(cat "$dir/pid" 2>/dev/null)
  saved=$(cat "$dir/process_start" 2>/dev/null)
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  checked=$((checked + 1))
  live=$(ps -p "$pid" -o lstart= 2>/dev/null || true)
  [ -n "$live" ] && [ "$live" = "$saved" ] || return 0
  job_id=$(basename "$dir")
  printf 'Stopping job %s (pid %s)...\n' "$job_id" "$pid"
  # Ask nicely first: the runner's own cleanup (stop its process group,
  # write exit_code, release the lock) is more thorough than anything
  # redone here, so give it a real chance to run before forcing anything.
  kill -TERM "$pid" 2>/dev/null
  deadline=$(( $(date +%s) + 5 ))
  while ps -p "$pid" >/dev/null 2>&1 && [ "$(date +%s)" -lt "$deadline" ]; do
    sleep 0.2
  done
  if ps -p "$pid" >/dev/null 2>&1; then
    forced=1
    kill -KILL "$pid" 2>/dev/null
    if [ -f "$dir/command_pid" ]; then
      cpid=$(cat "$dir/command_pid" 2>/dev/null)
      if [[ "$cpid" =~ ^[0-9]+$ ]]; then
        kill -TERM -- "-$cpid" 2>/dev/null
        sleep 1
        kill -KILL -- "-$cpid" 2>/dev/null
      fi
    fi
    # A killed runner never reaches its own cleanup trap; finish its
    # bookkeeping here so status/list do not show a job stuck "running".
    if [ ! -f "$dir/exit_code" ]; then
      date -u '+%Y-%m-%dT%H:%M:%SZ' > "$dir/finished_at"
      printf '137\n' > "$dir/exit_code"
    fi
    if [ "$(cat "$root/active/job" 2>/dev/null)" = "$job_id" ]; then
      rm -f "$root/active/job"
      rmdir "$root/active" 2>/dev/null || true
    fi
  fi
  stopped=$((stopped + 1))
  if [ "$forced" -eq 1 ]; then
    printf 'Job %s stopped (forced).\n' "$job_id"
  else
    printf 'Job %s stopped.\n' "$job_id"
  fi
}

printf 'MacBook Minion kill switch - checking %s\n\n' "$root"
if [ -d "$root/jobs" ]; then
  for dir in "$root"/jobs/*; do
    [ -d "$dir" ] || continue
    stop_job "$dir"
  done
fi

if [ "$stopped" -eq 0 ]; then
  printf 'Nothing was running. Checked %s job record(s).\n' "$checked"
else
  printf '\nStopped %s job(s).\n' "$stopped"
fi

printf '\nThis only stops work already running here; it does not block new\n'
printf 'connections from the other Mac. To be sure nothing new can start,\n'
printf 'turn off Remote Login: System Settings > General > Sharing > Remote\n'
printf 'Login. Turn it back on later to use this worker again -- you will\n'
printf 'not need to re-pair.\n'
