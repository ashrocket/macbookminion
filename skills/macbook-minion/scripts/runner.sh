#!/bin/bash
set -u
umask 077
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LC_ALL=C LANG=C LC_CTYPE=C
root=$1
job_id=$2
job_dir="$root/jobs/$job_id"
snapshot=$(cat "$job_dir/snapshot") || exit 1
command_pid=""
finish() {
  result=$?
  trap - EXIT
  if [ -n "$command_pid" ]; then
    # Stop only the job's own process group, never by name and never the
    # runner's or another job's group, so a script that left work in the
    # background cannot silently outlive the job and its released lock.
    kill -TERM -- "-$command_pid" 2>/dev/null
    if kill -0 -- "-$command_pid" 2>/dev/null; then
      sleep 1
      kill -KILL -- "-$command_pid" 2>/dev/null
      printf '%s\torphan_cleanup\t%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$command_pid" >> "$job_dir/worker_events.tsv"
    fi
  fi
  printf '%s\tfinished\t%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$result" >> "$job_dir/worker_events.tsv"
  if [ -f "$job_dir/retain_lock" ]; then
    printf '%s\tlock_retained\t\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$job_dir/worker_events.tsv"
  fi
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$job_dir/finished_at"
  printf '%s\n' "$result" > "$job_dir/exit_code.tmp"
  mv "$job_dir/exit_code.tmp" "$job_dir/exit_code"
  if [ ! -f "$job_dir/retain_lock" ] && [ "$(cat "$root/active/job" 2>/dev/null)" = "$job_id" ]; then
    # Only remove our own lock marker and now-empty directory; never job data.
    rm "$root/active/job"
    rmdir "$root/active"
  fi
  exit "$result"
}
trap finish EXIT
printf '%s\n' "$$" > "$job_dir/pid"
ps -p "$$" -o lstart= > "$job_dir/process_start"
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$job_dir/started_at"
printf '%s\tstarted\t\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$job_dir/worker_events.tsv"
export MINION_OUTPUT_DIR="$job_dir/output"
export MINION_JOB_ID="$job_id"
export MINION_EVENT_LOG="$job_dir/worker_events.tsv"
minion_note() {
  printf '%s\tnote\t%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$(printf '%s' "$*" | base64 | tr -d '\n')" >> "$MINION_EVENT_LOG"
}
export -f minion_note
cd "$root/snapshots/$snapshot" || exit 1
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i -w "$$" >/dev/null 2>&1 &
fi
# Job control gives the script its own process group (no setsid on macOS)
# so finish() can find and stop anything it left running.
set -m
/bin/bash "$job_dir/command.sh" &
command_pid=$!
printf '%s\n' "$command_pid" > "$job_dir/command_pid"
wait "$command_pid"
exit $?
