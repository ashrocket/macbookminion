# Contributing

Keep controller code in the Python standard library. Remote shell helpers must
work with macOS Bash 3.2. Changes to dispatch must retain the worker lock, stable
job IDs, reconciliation after uncertain replies, and checksum-verified retrieval.
Read [AGENTS.md](AGENTS.md) before changing the project.

Run `python3 -m unittest discover -s tests -v` and Bash syntax checks for edited
scripts. Test new routing policies with simulated metrics before using a real
worker. Identify which evidence is local simulation and which came from a live
worker; do not claim a fleet benchmark from a single-machine run.

Send a pull request with the changed behavior, relevant tests, and remaining
limitations. Keep private hostnames, keys, configurations, logs, datasets and
worker results out of contributions. See [SECURITY.md](SECURITY.md) for security
reports.
