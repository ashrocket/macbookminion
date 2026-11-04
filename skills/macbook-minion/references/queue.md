# Shared queue, messages and dashboard

All Codex sessions on the primary Mac use the installed skill's `scripts/minion.py`
with the existing `~/.config/macbookminion/config.json`. Do not create per-session
configs, dispatchers or ad-hoc SSH jobs. Additional workers are registered in this
same config with `worker-add`; see [fleet.md](fleet.md). Each host has one shared
queue and coordinator. The default state directory is beside that
config and keyed by worker host. Changing the config location or host alias makes
a separate ledger; it does not create more worker capacity.

## Submit and follow a job

After inspecting the worker profile, live readiness and input transfer plan:

```sh
python3 ~/.agents/skills/macbook-minion/scripts/minion.py put ./focused-input
python3 ~/.agents/skills/macbook-minion/scripts/minion.py run SNAPSHOT_ID \
  --script ./job.sh --owner PROJECT/SESSION_ID \
  --title 'Convert review clips' \
  --description 'Convert the approved source clips to preview MP4s and return checksummed files.'
python3 ~/.agents/skills/macbook-minion/scripts/minion.py queue
python3 ~/.agents/skills/macbook-minion/scripts/minion.py dashboard
python3 ~/.agents/skills/macbook-minion/scripts/minion.py status JOB_ID
python3 ~/.agents/skills/macbook-minion/scripts/minion.py logs JOB_ID
python3 ~/.agents/skills/macbook-minion/scripts/minion.py fetch JOB_ID ./new-results
```

Always supply a real `PROJECT/SESSION_ID`, meaningful title and description. The
CLI has defaults for manual use, but agents should describe the purpose, intended
output and relevant scope. Keep secrets and unrelated personal data out of titles,
descriptions and logs. Preserve returned job IDs in the originating session.

Submission freezes and hashes the script and runner, saves the request in a
private SQLite queue, then starts or reuses one local coordinator process. A
returned `queued` receipt means the request is saved; it does not prove remote
execution. Jobs dispatch in committed submission order and share the worker's
atomic single-job lock. Other sessions may enqueue while the worker is busy.
They retain separate conversations, ownership and result directories.

Browser commands accept the same owner, title and description flags. They probe
and upload their bounded plan before enqueueing, so submitting a new browser job
still needs a reachable worker. Shell jobs using an existing snapshot can be
saved while offline; dispatch waits for a verified connection.

Inside a Bash job, call `minion_note 'Finished decoding the first clip'` to append
a timestamped progress message. Write normal output to stdout/stderr and
deliverables to `$MINION_OUTPUT_DIR`. The log is not a secret scrubber.

## Dashboard and audit

`dashboard` prints a private URL on `127.0.0.1`. Open it on the primary Mac. It shows
sender owners, queue positions, active worker, job descriptions, start/finish times,
exit codes, request/reply traces, worker progress and the last 120 output lines.
Search descriptions or owners, filter states, select a job, expand message details,
or switch to Output. Live updates can be paused. Older messages remain available.

The page is read-only; cancellation and recovery stay in the CLI. It has no external
assets and does not expose a LAN browser-control port. Its private URL grants local
read access to job metadata and output: do not publish it or forward the port.

```sh
python3 ~/.agents/skills/macbook-minion/scripts/minion.py audit --job JOB_ID
python3 ~/.agents/skills/macbook-minion/scripts/minion.py audit --verify
python3 ~/.agents/skills/macbook-minion/scripts/minion.py audit --export ./new-audit.jsonl
```

The append-only ledger records sender requests, queue state, correlated SSH and
transfer requests/replies, timestamps, hashes, worker lifecycle/progress and verified
retrieval. Transport records store safe identifiers, sizes and hashes instead of
raw command contents or raw replies. Job scripts can still deliberately log private
data; review before sharing output or exports. Worker timestamps and controller
observation times are distinct; the machines' clocks may differ.

SQLite transactions protect concurrent writers. A SHA-256 chain detects changed
events and verifies the exported sequence. It is a local integrity check, not an
externally anchored, tamper-proof audit. Older remote jobs are imported with an
explicit history gap; their earlier messages are not invented. Ledger, spooled
scripts and worker artifacts are retained without automatic pruning or quotas.

## Recovery and ownership

- `cancel JOB_ID` cancels only a queued, undispatched job, atomically. It does not
  terminate a running process or delete artifacts.
- `controller-stop` requests the local service to stop. Remote work continues,
  and queued jobs remain saved. `dashboard` or the next submission starts it again.
  Closing the controlling chat or browser does not stop the detached service.
  Without an explicit `controller-install`, no login service is installed; after
  a primary-Mac reboot, run `dashboard` or submit a job to resume dispatch. With
  the opt-in LaunchAgent, login starts this same coordinator. `controller-stop`
  unloads it for the current login and reconciles any foreground coordinator;
  `dashboard` starts it again. Config, queues and remote jobs are preserved.
- Connection loss pauses new dispatch. The coordinator reconciles the same job ID
  before retrying incomplete staging, at most three attempts. A recorded launch
  is never sent again automatically, even when the original reply was lost.
- `attention` means inspect the recorded error, remote status and logs. Interrupted
  processes, missing process evidence and uncertain browser cleanup keep ownership
  for review. Never remove another job's lock to make the queue move.
- `retry-dispatch JOB_ID` is only for a reconciled, managed job that has not launched.
  Resolve the underlying staging problem first. Completed or launched jobs cannot
  be retried this way. A new job is a new execution requiring the original task's
  authorization; browser submissions are never repeated automatically.

The coordinator uses a local process lock; the worker lock remains authoritative.
Remote jobs run as the SSH account, not inside an OS sandbox. Use normal task
permissions. The queue does not grant deployment, purchase, login or send authority.

## Local context and retained ownership

`doctor` reports local user/bootstrap failure separately from worker reachability.
Read [local-context.md](local-context.md) before selecting a recovery path.

For a finished job with uncertain browser cleanup, run `lock-inspect JOB_ID`.
Only after explicit owner authorization, use `recover-lock JOB_ID
--expected-owner ORIGINAL_PROJECT/SESSION_ID --owner RECOVERY_PROJECT/SESSION_ID`.
The command requires a later worker reboot, exact original owner, and preserved
finished/retained evidence. It archives the exact active lock within the original
job, preserves exit status and outputs, and records the recovery actor. It never
reruns the job. A durable reservation guard blocks new dispatch until recovery
finishes. Interrupted recovery requires inspection and a subsequent attended
reboot before resuming the same operation. Never remove that guard manually.
