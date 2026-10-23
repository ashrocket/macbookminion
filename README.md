# MacBook Minion

A Codex skill and SSH harness for offloading independent jobs and disposable
scratch files to an Intel MacBook. No server agent, Python, Docker, or Codex
installation is required on the older Mac for pairing and shell jobs. The
controlling Mac uses Python 3.8+. Optional browser jobs use the worker's installed
Python and Chrome plus a workspace-local ChromeDriver.

Start with the [pairing guide](skills/macbook-minion/references/pairing.md).
Wi-Fi is enough for initial pairing; Ethernet helps with large transfers. The
first live inventory determines what the older machine can usefully run.

The owner's laptop is now paired and has passed live transfer, background shell,
Python/FFmpeg, and checksum-verified retrieval checks. See the dated
[worker profile](skills/macbook-minion/references/worker.md) for evidence and
current limits. A headless Chrome adapter now supports bounded browser jobs;
see the [browser guide](skills/macbook-minion/references/browser.md).
Multiple Codex sessions share a persistent queue and one coordinator, while
retaining separate conversations. A private local dashboard shows job descriptions,
ownership, queue positions, progress, output and correlated request/reply messages.
See the [queue and audit guide](skills/macbook-minion/references/queue.md).

```sh
./bin/minion doctor
./bin/minion --help
./bin/minion plan examples/smoke-input
./bin/minion browser-probe
./bin/minion browser-selftest --owner PROJECT/SESSION_ID
./bin/minion browser-run examples/browser-smoke.json --owner PROJECT/SESSION_ID
./bin/minion queue
./bin/minion dashboard
./bin/minion audit --verify
```

The harness provides a read-only hardware/tool probe, strict SSH host checks,
filtered file plans, unique snapshots with SHA-256 verification, one background
job at a time, persistent logs and exit status, and verified result retrieval.
New jobs are saved before dispatch, with a stable ID and immutable script hashes.
The coordinator survives closing the chat. Restarting it reconciles existing work
before dispatch; it never repeats a recorded launch. Give each submission a useful
`--title`, `--description`, and real `--owner PROJECT/SESSION_ID`.
Its local configuration is separate from the source tree. It never deletes local
inputs or treats disposable worker storage as a backup.

Browser jobs open pages, fill ordinary fields, click, verify text, and return
screenshots, HTML and text. Each job uses a fresh profile, loopback-only control,
a deadline and verified cleanup. They do not attach to the owner's personal
browser or provide persistent signed-in sessions. A failed cleanup retains the
shared lock for inspection. Review the worker's dated evidence and network limits
before selecting a browser task.

The [skill](skills/macbook-minion/SKILL.md) teaches Codex when to offload, how to
reconcile interrupted connections, and what constitutes verified completion.
Copy `skills/macbook-minion` to `~/.agents/skills/macbook-minion` for user-wide
discovery. Do not overwrite a pre-existing skill without reviewing its changes.

Jobs run with the SSH user's permissions. The directory is **not a security
sandbox**. Use a dedicated standard macOS account, keep remote credentials absent,
and grant only the access required for approved workloads. The free-space floor is
a preflight check, not a quota. No speedup or Intel compatibility is claimed until
a representative task is measured on the actual laptop.

Pairing also installs a local, offline kill switch on the worker itself
(`~/.macbookminion/kill-switch.sh`, or double-click `~/Desktop/Stop-MacBook-Minion.command`)
so the owner can force-stop anything running there right now while sitting at
that Mac, with no network access and no dependency on this controller. It
does not block new connections; turning off Remote Login in Sharing settings
is what actually does that.

Run local behavioral verification:

```sh
python3 -m unittest discover -s tests -v
python3 path/to/skill-creator/scripts/quick_validate.py skills/macbook-minion
```

`quick_validate.py` ships with the skill-creator tooling in your Codex
skills installation, not with this repository; substitute its
actual path on your machine.

Tests use disposable local directories, the system Bash, and rsync; they do not
prove pairing, live SSH behavior, or compatibility with the older Mac's exact OS.
