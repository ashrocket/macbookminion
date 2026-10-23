# Choose useful work

The user wants greater capability, responsiveness, and safety, **not backups**.
Codex stays on the controlling Mac. The older Mac contributes independent CPU,
RAM, an Intel test environment, and disposable disk space. Its speed and current
tool compatibility must be measured; it may be slower for individual jobs.

| Candidate | Benefit | Check first |
| --- | --- | --- |
| Independent tests and lint | Frees the primary Mac while Codex edits | Supported runtime and dependency versions |
| Intel-compatible builds | Checks behavior specific to x86_64 | macOS/SDK/compiler compatibility |
| PDF/media conversion | Moves long CPU and disk work off the primary Mac | Installed tool, inputs, expected output size |
| Bounded indexing or data batches | Keeps scratch work away from active projects | Dataset privacy, memory and disk estimate |
| Temporary test fixtures | Disposable data near the worker | No credentials or production access |
| Bounded headless browser checks | Moves rendering and test execution off the primary Mac | Read [browser.md](browser.md), check driver and destination readiness |

Avoid assuming current Xcode, Docker Desktop, browsers, Node, or Python support
the installed macOS. The probe reports executable paths, not compatibility. Check
actual versions for the selected workload before installing or running it. A
working tool's presence does not make every repository compatible.

Keep build dependencies architecture-specific; transfer source and install only
approved dependencies on the worker. A source snapshot excludes `.git`, so jobs
that require repository metadata need a separately authorized checkout strategy.
Symlinks and empty directories are omitted. Files with newlines or backslashes in
their names are rejected. There is intentionally no general home-directory sync.

Write scripts with `set -euo pipefail`. The harness records the script's exit code;
it cannot infer whether a script silently ignored an earlier failure. Deliverables
go into `$MINION_OUTPUT_DIR`. Do not leave detached child processes behind: job
completion and result verification assume the script waits for its children.
Artifacts written elsewhere in the snapshot can be retrieved with `get` after all
jobs finish. Scripts receive no controller environment variables or auth files.
They can still access anything their remote account can access.

Default policy is one active job per worker, 25 GiB kept free at preflight, and an additional
5 GiB estimated output allowance. With roughly 100 GB free, start with a few small
jobs and measure actual growth. These settings are not a storage quota. There is
no remote persistent service, bulk cleanup, automatic package installation, or
Codex model running on the older Mac. A primary-side coordinator manages each
worker's durable queue. Browser setup explicitly installs its driver;
browser jobs clean up only their generated profiles and processes.
