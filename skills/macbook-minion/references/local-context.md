# Local coordinator context and LaunchAgent

The local context probe is a current observation. A failed UID, Aqua bootstrap,
or name-service lookup does not by itself establish which parent process caused
it. Run the same probe from a healthy Terminal session before choosing recovery.

LaunchAgent installation is explicit. It uses the existing coordinator command,
queue, state directory, and `service.lock`; it does not create a second dispatcher.
Installation refuses an existing legacy coordinator instead of terminating it.
The coordinator must finish acquiring `service.lock` before calling
`launch_agent.configure_runtime(config)`.

The private installation metadata pins the interpreter selected during the first
installation, the exact script/config/worker/host arguments, and state directory.
Later CLI invocations may use another Python version without changing the saved
interpreter. Missing or non-executable interpreters require explicit repair.
Metadata and plist bytes must match their canonical forms; neither is silently
rewritten to accept drift. These are integrity checks for accidental drift, not
protection against a user who can deliberately edit both files.

Lifecycle operations check local context, take a private per-installation lock,
and recheck canonical files before launchctl mutations. New files are published
atomically without replacing an existing file. Start bootstraps an unloaded
service and uses kickstart without `-k`. Stop uses bootout and preserves metadata,
plist, config, queue, and worker jobs. The existing coordinator lock resolves a
race with a separately started legacy coordinator.

The plist sets the private state directory as its working directory and umask
077. Launchd stdout/stderr go to `/dev/null`. After obtaining the coordinator
lock, Python diagnostics use private `controller.log` and `controller.log.1`,
each bounded to 1 MiB. Rotation discards older diagnostics; it does not alter the
job ledger or audit records. Legacy oversized diagnostic files are bounded on
the next write. Symlinked, shared, or wrongly owned log files are refused.

Unit tests use temporary directories and mocked launchctl. They do not establish
live macOS bootstrap behavior, login restart behavior, or worker job completion.

From a healthy, authorized macOS user session, use the canonical installed skill:

```sh
python3 ~/.agents/skills/macbook-minion/scripts/minion.py doctor
python3 ~/.agents/skills/macbook-minion/scripts/minion.py controller-stop
python3 ~/.agents/skills/macbook-minion/scripts/minion.py controller-install
python3 ~/.agents/skills/macbook-minion/scripts/minion.py dashboard
```

Wait for the existing coordinator to release its service lock before installation.
The returned installation result proves launchctl accepted the job; confirm the
dashboard health and worker probe separately. The LaunchAgent grants no additional
task permission and must never be used to route around a denied tool action.
