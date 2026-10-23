# MacBook Minion

Build a Codex-controlled SSH worker for the user's Intel MacBook. Its purpose is
independent background work and disposable scratch storage, not backups.

- Keep the controller Python standard-library only; remote helpers must work with
  macOS Bash 3.2, SSH, rsync, shasum, and built-in utilities. Do not require Python,
  Node, Docker, or Codex on the worker just to pair or inspect it.
- Canonical skill source is `skills/macbook-minion/`. `bin/minion` uses that source.
- All sessions submit through the shared persistent queue/config. Supply a real
  session owner, title and description, preserve job IDs, and use `queue`,
  `dashboard` and `audit` for evidence. Never bypass the coordinator or worker lock.
- Probe before changing a real worker. Preserve host-key checking, disable agent
  forwarding, and never transfer credentials or delete source files automatically.
- Jobs run with the SSH account's permissions. A working directory is not a sandbox.
  Recommend a dedicated standard macOS account for stronger separation.
- Report local tests separately from SSH and Intel/macOS verification. No claim of
  pairing, remote completion, speedup, or disk reclamation without live evidence.
- Run `python3 -m unittest discover -s tests -v` and the skill validator after edits.
  Review shell quoting, transfer boundaries, job status, and failure recovery.
