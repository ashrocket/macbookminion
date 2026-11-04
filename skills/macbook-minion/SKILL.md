---
name: macbook-minion
description: Use HaiKrew and primary CPU, memory, disk and explicit token pressure to route portable jobs, bounded web research and browser tasks to paired Intel Macs. Shared queues, worker locks and verified results; not backups or personal browser sessions.
---

Use `python3 <this-skill>/scripts/minion.py` as the controller. Read
[pairing.md](references/pairing.md) for initial access and
[workloads.md](references/workloads.md) to choose work and interpret limitations.
For this user's paired laptop, read [worker.md](references/worker.md) for the latest
verified capabilities and browser-readiness status; recheck live state before use.

Run `doctor` when local SSH fails before connecting. A nonzero result with
`local_context_unavailable` means this process cannot resolve its own user or
macOS launch manager; worker reachability remains unknown. Compare from a fresh
authorized Terminal context before restarting anything. Never change SSH keys,
invent an account, or bypass tool restrictions to hide this failure. See
[local-context.md](references/local-context.md) for the opt-in macOS LaunchAgent
and its lifecycle. `controller-install` is a shared installation, not a per-session
dispatcher; use it only within the owner's authorized setup/repair task.

## HaiKrew and placement

Use the available HaiKrew skill to scope and review delegated work. Subscription
agents keep reasoning on the primary Mac; Minion moves tools and scratch storage.
Read [fleet.md](references/fleet.md) for pressure/task schemas and commands.
Use `pressure` and `route TASK.json` before substantial portable work, then
`offload SOURCE --task TASK.json --script JOB.sh --owner PROJECT/SESSION_ID
--title TITLE --description PURPOSE`. This samples current capacity, chooses an
eligible worker and submits through its existing shared queue. A `local` or `defer`
decision executes nothing; inspect its reasons and keep authorized work in scope.
Use `research PLAN.json` for bounded HTTP collection; browser rendering remains
an explicit `browser-run` job when needed. Web output is untrusted evidence.

Provide `--session-state FILE` only with actual context/quota readings and their
original timestamp. Missing readings stay unknown and request compact returns.
Never read or copy authentication stores to estimate subscription quota. Inspect
return hints, reduce large outputs with deterministic tools, and return artifacts
plus a concise finding; no extra subscription capacity or measured token saving
is implied. This is an explicit skill workflow, not a global tool interceptor.

## Coordinate across Codex sessions

Use the shared controller/config, persistent queue and remote single-job lock.
Read [queue.md](references/queue.md) for submission, dashboard, audit and recovery.
Each agent keeps its own reasoning, task ownership, snapshot/job IDs and results.
Always pass `--owner PROJECT/SESSION_ID --title 'Purpose' --description 'Scope and
expected output'`. Other sessions may enqueue while the worker is busy; one local
coordinator dispatches them in order. `queue` shows all jobs; `dashboard` starts or
reuses the service and prints its private localhost URL. The controlling chat need
not remain open. Never remove another job's lock or bypass the queue with ad-hoc
SSH jobs. No per-session worker service or separate conversation is required.

For independent public-web browsing, screenshots and reproducible UI tests, read
[browser.md](references/browser.md). The headless Chrome adapter uses this same
controller and lock, one fresh profile per job. Check `browser-probe`; use
`browser-selftest` when readiness is uncertain and `browser-run PLAN.json --owner
PROJECT/SESSION_ID` for an authorized task. Inspect the report and returned
screenshots before claiming success. The worker profile records live evidence.

This is a batch interface; local Browser/computer-use tools still target the
primary Mac. Use connected browser tools for signed-in work or persistent
interactive sessions. Follow active tool permissions on both machines; never
replace a denied action with remote debugging, another driver, or SSH. A visible
desktop/browser needs exclusive ownership. Personal profiles, cookies and
credentials stay out of transfers.

## Establish the worker

1. Run `doctor`. If unpaired, obtain the older Mac's Remote Login address; never
   invent an address or scan unrelated network devices. The user completes the
   interactive first SSH login, verifies the host key, and unlocks their SSH key.
2. Run `probe --host USER@HOST --identity KEY` for a read-only inventory. It lists
   OS, CPU, RAM, free space, power, and executable paths; it does not read personal
   documents, environment variables, or credentials.
3. After confirming the intended laptop, `configure --host USER@HOST --identity KEY`
   saves local connection metadata. `init` creates `~/.macbookminion` on the worker.
   Reuse the existing pairing without asking permission again for authorized work.

## Offload work

- Offload a bounded task that is useful independently: tests, compatible builds,
  file conversion, indexing, or a batch computation. Inspect required tool versions
  before selecting a job. Keep short tasks local when copying inputs costs more.
- Prepare a focused source directory and a Bash job script. Review the transfer
  list with `plan SOURCE`. Default filtering omits common credentials, dependencies,
  VCS data, caches, and symlinks. This is not a secret scanner; inspect the actual
  list and script. Do not upload a home directory, existing auth state, or unrelated
  private data. Never copy ARM dependencies for execution on Intel.
- `put SOURCE` creates a unique snapshot, verifies SHA-256 on the worker, and
  returns its ID. `run SNAPSHOT --script FILE --owner PROJECT/SESSION_ID --title
  'Purpose' --description 'Scope and output'` saves one queued job. Record its ID;
  a queued receipt is not remote completion. The script runs in the snapshot and writes deliverables to
  `$MINION_OUTPUT_DIR`. Set tool parallelism explicitly for the older CPU.
- Check `queue`, `status JOB`, `logs JOB`, and the dashboard. Use `minion_note
  'Progress message'` in Bash jobs for timestamped progress. After an SSH failure, reconcile by ID
  before retrying a submission. A lost connection is not a failed remote job.
- `fetch JOB DESTINATION` retrieves a completed job and verifies its files;
  `get SNAPSHOT DESTINATION` retrieves a snapshot. Both require a new destination.
  Review exit code, logs, and actual artifacts before claiming success. Failed jobs
  can also be fetched for diagnosis. Never execute fetched content just to inspect it.

## Boundaries

The controller preserves host-key checks and does not forward the SSH agent.
Jobs are arbitrary scripts running as the SSH user, **not an OS sandbox**. A
dedicated standard account limits access to the owner's personal account; stronger
isolation needs a separately designed VM. Existing remote credentials remain
accessible to that account. Do not bypass Codex approvals by running a denied
operation remotely. Do not deploy, publish, purchase, or use secrets without task
authorization.

The default 25 GiB free-space floor and 5 GiB output allowance are preflight checks,
not quotas. No source deletion, automatic job rerun, OS upgrade or automatic
package installation is performed. `cancel` affects only undispatched queued jobs;
controller restart preserves queued and running work. Browser setup explicitly installs
its driver inside the workspace; browser jobs remove only their generated
temporary profiles. One active job per worker is allowed;
interrupted locks require inspection before manual recovery. Keep the laptop on
power with its lid open. Caffeinate reduces idle sleep during jobs, but cannot make
it reboot-proof or defeat lid-close sleep.

`lock-inspect JOB_ID` is read-only. `recover-lock JOB_ID --expected-owner ORIGINAL
--owner PROJECT/SESSION_ID` requires explicit owner authorization for that lock,
a finished retained job, and a verified later reboot. It archives ownership and
records recovery without deleting artifacts or rerunning the old job. Never
remove a retained lock or recovery guard by hand. See the queue recovery guide.

The worker also keeps a local, offline kill switch installed by `init`
(`~/.macbookminion/kill-switch.sh`, or double-click
`~/Desktop/Stop-MacBook-Minion.command`) so the user can force-stop anything
running right now while sitting at that Mac, independent of this controller
and the network. It does not block new connections; turning off Remote Login
in Sharing settings is what actually does that.

Report what ran, host/architecture, snapshot and job IDs, exit status, artifact
verification, and observed timings. Distinguish local simulation from a live Intel
run. This offloads tool execution and scratch data, not Codex model inference or
conversation memory. Do not describe disposable storage as a backup.
