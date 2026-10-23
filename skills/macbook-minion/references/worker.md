# Paired worker: example acceptance record (2026-09-25)

This file is a worked example of what a verified pairing baseline looks like,
kept from this project's own initial acceptance run against a real Intel
MacBook Pro. Replace it with your own dated baseline after pairing your
worker: rerun `probe`, `list`, and the acceptance steps in pairing.md, then
overwrite this file (or keep your live version local; it never has to match
the example below). Do not put your worker's real address, or anyone else's,
in a file you intend to publish.

This is a dated baseline, not a live availability guarantee. The shared local
configuration is `~/.config/macbookminion/config.json`. Use `probe` and `list` to
check the current machine and active jobs. The configured destination in this
example was `minion@older-mac.local`; `.lan` did not resolve during pairing.

## Verified capabilities

- MacBookPro11,4, macOS 12.7.6, x86_64.
- Intel i7-4770HQ at 2.20 GHz: 4 physical cores, 8 logical CPUs; 16 GiB RAM.
- Approximately 134 GiB free and AC power at verification time.
- Python 3.13.2, Git 2.46.0, FFmpeg 8.0; built-in Bash, rsync, shasum, caffeinate.
- Node/npm unavailable on the runner PATH. The selected developer directory is
  `/Library/Developer/CommandLineTools`; do not infer a usable full Xcode install.
- Strict SSH host checking and the dedicated key work noninteractively. If your
  key has no passphrase, batch automation works without `ssh-add` after each
  reboot; either way, never expose or copy the private key.
- The SSH account is the owner's existing account. No separate standard account
  or OS sandbox has been configured. Workspace organization does not restrict the
  account's filesystem access. Do not infer Full Disk Access or Remote Management
  settings from SSH reachability.

## Live acceptance evidence

Snapshot: `s-4b38d28d3b4f4cfcb7f14060ef14ddd8`, one 28-byte test input, SHA-256
verified on arrival.

| Job | Result | Retrieved files |
| --- | --- | --- |
| `j-d2d82779d4a84c7ca721093971c8c475` | Shell smoke test, exit 0, Darwin x86_64 | 10, all checksums verified |
| `j-a300bc4d434646a1be49139129a065ff` | FFmpeg generated and decoded a five-second AAC tone; Python produced its report; exit 0 | 11, all checksums verified |
| `j-361aad249aa543859971f966fe30bc89` | Final shell test with owning Codex session recorded; exit 0; locale warnings removed | 11, all checksums verified |

Retrieved evidence is in the project at `results/2026-09-25-smoke` and
`results/2026-09-25-media`, plus `results/2026-09-25-owner`. The controller's 14 local
behavioral tests and skill validation also passed. These are synthetic test assets,
not user data. These tests do not
establish a speedup over the primary Mac. Select a representative workload
before claiming a performance benefit.

## Browser readiness: HEADLESS BATCH JOBS VERIFIED

Chrome 150.0.7871.125 with ChromeDriver 150.0.7871.124 passed live tests on macOS
12.7.6 / x86_64. Python 3.13.2 runs the adapter without extra Python packages.
The driver is installed only inside `~/.macbookminion/browser`. Its receipt records
Google's official HTTPS source and hashes; this vendor binary is unsigned, and no
Gatekeeper or quarantine settings were changed. Firefox and Safari metadata were
inspected previously, but neither has been configured or tested for automation.

Read [browser.md](browser.md). Use `browser-probe`, `browser-selftest` and
`browser-run PLAN.json --owner PROJECT/SESSION_ID`, followed by normal status/logs/
fetch. The adapter uses the shared lock, an empty disposable profile, a deadline,
and loopback-only driver control. Local Browser/computer-use tools still target
the primary machine; persistent signed-in browsing is not provided by this adapter.

| Job | Verified result |
| --- | --- |
| `j-8896a023cb824a14a9ccadecf579a690` | Driver installation, exit 0; installation receipt retrieved and checksummed |
| `j-957bb48c661f48738645db73318776c0` | Self-contained test form: open, type, click, assert result, before/after screenshots; all 7 steps passed, exit 0 |
| `j-fe23c35e49034eaebcb16c49499a5785` | Public Selenium test form: filled and submitted dummy text, verified Received!, captured screenshots; all 7 steps passed, exit 0 |
| `j-ab0aeba2457f4c23b185b712f88951b7` | Deliberate 10-second timeout: failed as expected with exit 1, returned failure capture, stopped its process group, removed profile and released lock |

Both browser tests verified loopback listeners, stopped their process groups,
removed their profiles, and released ownership. Each returned 20 files with all
SHA-256 checks passing. Screenshots were visually inspected. Browser runtime was
1.960 and 2.424 seconds respectively, excluding submission/transfer; these are
functional checks, not evidence of a speedup over the primary Mac.

Evidence is in `results/2026-09-25-browser-installed`,
`results/2026-09-25-browser-selftest`, and `results/2026-09-25-browser-public`.
The timeout test returned 16 checksummed files in
`results/2026-09-25-browser-timeout`; elapsed runtime including cleanup was 10.673
seconds, confirming the deadline path on the actual worker.
An earlier browser attempt failed before any page action with DNS resolution
failure; its cleanup passed. Subsequent DNS/HTTPS checks reached Apple and
Selenium, and the Selenium browser test passed. `example.com` failed to resolve
during diagnosis. Treat destination availability as live state; do not retry
submissions automatically or assume every public site is reachable.

That browser release passed 24 local behavioral tests, including shared lock
retention when browser cleanup is uncertain.

## Shared queue and audit acceptance (2026-09-25)

The installed skill now includes a persistent FIFO queue, one local coordinator,
job titles/descriptions, timestamped worker progress, a request/reply audit and
a private read-only dashboard. Read [queue.md](queue.md). All Codex sessions use
the existing shared config and preserve their own owner and job IDs.

Three queued acceptance jobs ran on this Intel Mac in order, without overlap:

| Job | Verified result |
| --- | --- |
| `j-0ba5d2d554b04f6591bdf322dccd7498` | Progress job, exit 0; controller stopped and restarted while the same remote PID continued; one execution recorded; 17 files retrieved and verified |
| `j-b089078ac2634d5cbb6922c922200b43` | Browser selftest waited behind it, then passed all 7 steps, exit 0; 25 files retrieved and verified; screenshot inspected |
| `j-c690f060504c4a9da9345deb82612eac` | Deliberate exit 7 appeared as failed and released ownership; 15 files retrieved and verified |

The shell job ran 16:12:17–16:14:27 UTC; browser 16:15:29–16:15:32;
expected failure 16:15:41–16:15:41. The browser confirmed process-group cleanup,
profile removal and loopback-only listeners. The controller restart preserved
pending work and the running process. These are functional acceptance timings,
not a performance comparison. Three test owner labels exercised routing; they
were submitted by one Codex session, not three independent agents.

Evidence is under `results/2026-09-25-queue/`, including the restart record,
checksummed results and exported audit. The current suite has 39 passing local
tests covering concurrent queue writers, FIFO claims, lost replies, staging
recovery, offline waiting, retained locks, cancellation and dashboard access.
The dashboard was inspected in Chrome against the live controller: running and
waiting jobs, descriptions, progress, output, failure filtering and audit checking.
Availability remains live state; run `queue` and `browser-probe` before use.
