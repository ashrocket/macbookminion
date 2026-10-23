# Keeping Intel workers useful and maintainable

This guide was researched on **2026-10-23**. It is a maintenance proposal, not an
installation record. Recheck upstream releases before every change. The existing
worker was verified on that date as a MacBookPro11,4 with macOS 12.7.6, an Intel
i7-4770HQ, 4 physical/8 logical cores and 16 GiB RAM. Python 3.13.2 and absent Node
were confirmed by the new checksum-verified inventory. It also found Ruby 2.6.10,
no PyTorch, OpenSSL 3.0.15 in Python, and OCLP application 2.5.1. SIP and
authenticated-root were enabled; the two boot-version NVRAM keys were absent.
Those missing keys do not by themselves establish the complete boot configuration.
The owner chose to handle attended OS maintenance later.

For exact Python and Node artifacts that can be evaluated on Monterey before an
OS upgrade, and the dependency work needed for Ruby, see
[runtime maintenance options](runtime-maintenance-options.md). Node 22's binary
minimum differs from the Node 24 target below; compatibility does not establish
that the host OS meets upstream security-support policy.

A newer runtime does not establish that its host OS, browser or dependencies are
securely patched. Record each layer separately, including a source and check date.
Run public-web workloads only after reviewing the host and browser maintenance
state. A version probe alone is not a vulnerability assessment.

## Compatibility choices

| Layer | Upstream evidence at review | Worker decision |
| --- | --- | --- |
| OCLP | [Supported models](https://dortania.github.io/OpenCore-Legacy-Patcher/MODELS.html) includes MacBookPro11,4; native firmware updates are recommended first. | Inspect the exact model, GPU and target build before selecting an upgrade. |
| macOS through OCLP | [Releases](https://github.com/dortania/OpenCore-Legacy-Patcher/releases) lists 2.5.1 as latest stable and 3.0.0-rc.3 as prerelease introducing Tahoe. The [FAQ](https://dortania.github.io/OpenCore-Legacy-Patcher/FAQ.html) still describes Big Sur through Sequoia. | Use the stable release's documented OS/build coverage. Do not turn the repository's broader Tahoe claim into a stable-support guarantee. |
| Python | [Lifecycle](https://devguide.python.org/versions/) lists 3.14/3.15 in bugfix maintenance and 3.11–3.13 in security maintenance. [macOS downloads](https://www.python.org/downloads/macos/) lists 3.15.1, 3.14.9 and 3.13.17 dated October 21. | Prefer the newest compatible patch in 3.14 or 3.15 after checking dependency wheels. Historical 3.13.2 is not an up-to-date patch. |
| Python packaging | [Python's macOS guide](https://docs.python.org/3.15/using/mac.html) describes signed/notarized universal2 installers for Intel and Apple Silicon, typically supporting macOS 10.15 onward. | Inspect the selected installer's actual minimum OS and signatures. Use separate virtual environments and never replace Apple's Python. |
| Node.js | [Release status](https://nodejs.org/en/about/previous-releases) lists 24 and 22 as LTS, 26 as Current, 20 as EOL. [Node 24 platform requirements](https://github.com/nodejs/node/blob/v24.x/BUILDING.md) require macOS 13.5+ for x64. | Target latest compatible 24.x LTS security patch after OS maintenance; unchanged Monterey fails this requirement. Do not silently substitute EOL Node. |
| Ruby | [Maintenance branches](https://www.ruby-lang.org/en/downloads/branches/) lists 4.0/3.4 in normal maintenance, 3.3 in security maintenance, 3.2 EOL. [Downloads](https://www.ruby-lang.org/en/downloads/) identifies 4.0.9 as stable. | Test latest compatible 4.0 or 3.4 in an isolated toolchain. Source/build dependencies need their own checks; leave system Ruby alone. |
| PyTorch on Intel macOS | [Upstream deprecation](https://github.com/pytorch/pytorch/issues/114602) and the [official wheel index](https://download.pytorch.org/whl/cpu/torch/) show the architecture split: 2.14.1 macOS wheels are arm64, while Linux x86_64 CPU wheels exist. | No current official Intel macOS binary path established. OCLP cannot change the CPU architecture. Do not present torch 2.2.2 as a current patched solution. |
| Homebrew | [Support tiers](https://docs.brew.sh/Support-Tiers) classifies Intel macOS as Tier 3 and says new Intel bottles have stopped. | Do not assume `brew upgrade` will provide a supported runtime stack; an OS upgrade does not restore Intel Tier 1 support. |

For current PyTorch on this Intel hardware, prefer a separately approved migration
to a maintained Linux distribution with compatible CPU instructions, Python and
glibc. Check the official wheel tags and run a small CPU tensor operation before
routing real jobs. This is a recommendation, not evidence that this harness has
been validated on Linux. Keep Linux migration, disk changes and dual boot outside
ordinary queued jobs. A community macOS source build has a separate maintenance
burden and does not inherit official binary support.

## Inventory first

Submit [runtime-inventory.sh](../examples/runtime-inventory.sh) through the existing
shared queue and config, using a real owner and a descriptive job title. Follow the
[queue guide](../skills/macbook-minion/references/queue.md) to stage inputs, submit,
inspect the returned job ID, and fetch verified output. The script works with Bash
3.2 and needs no runtime just to inspect the Mac. It reports optional Python
package metadata without importing PyTorch; missing tools remain explicit.

For a local, read-only inspection while physically at the worker:

```sh
/bin/bash examples/runtime-inventory.sh
sw_vers
sysctl -n hw.model
csrutil status
csrutil authenticated-root status
nvram 4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102:opencore-version
nvram 4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102:OCLP-Version
```

The two named NVRAM keys come from the [OCLP update guide](https://dortania.github.io/OpenCore-Legacy-Patcher/UPDATE.html).
Do not dump all NVRAM, environment variables, browser profiles or account details.
Inspect root patches in OCLP's **Post Install Volume Patch** screen. Missing NVRAM
keys are not proof of a stock boot configuration. Keep raw inventories private:
runtime executable paths can include account names. The script only inspects the
job's environment and common application locations; it is not an exhaustive scan
of every virtual environment or alternate runtime installation.

## An attended maintenance window

1. Drain and reconcile the shared queue. Record active jobs and retrieve their
   outputs before maintenance; do not remove another job's lock. Have someone at
   the worker with power, keyboard and a tested way to reach its boot picker.
2. Verify an external backup by restoring a sample. Record the current bootable
   OS, exact OCLP app/bootloader versions, patch state and selected recovery media.
   Test booting that media without erasing or installing. Keep recovery credentials
   with the owner, outside job inputs and logs. Worker scratch is never a backup.
3. Choose the precise stable OS build and OCLP release after reading its known
   issues. Plan capacity for the installer, backup and recovery; a free-space probe
   is not permission to clean unrelated files. On the primary Mac, use the owner's
   diskcleaner workflow before large downloads or builds.
4. Follow the [upstream update sequence](https://dortania.github.io/OpenCore-Legacy-Patcher/UPDATE.html):
   app, bootloader and applicable root patches are distinct components. The owner
   performs the OS installation and reboot in the attended window. This harness
   does not install OCLP, change SIP, apply root patches or reboot automatically.
5. Confirm graphical login, graphics, networking, sleep/wake and SSH after reboot.
   Inspect root patches and capture a second inventory. OCLP documents that OS
   updates replace root patches and that unsupported Ventura-and-newer systems
   require lowered SIP. Do not enable or disable SIP as a generic repair; consult
   the model-specific [post-install guide](https://dortania.github.io/OpenCore-Legacy-Patcher/POST-INSTALL.html).
6. Install approved runtimes separately from the OS change, preserving the old
   working environment. Verify the download provenance, signature/checksum and
   selected version. Rebuild a new virtual environment or toolchain from reviewed
   lockfiles. Run representative tests, then switch the job's explicit executable
   path. Only resume public-web work after browser and runtime checks pass.

## Rollback and acceptance

For runtime rollback, restore the job's previous explicit executable/environment
path and lockfile, then rerun the focused test. Keep the old environment until the
new one has passed its acceptance jobs. Avoid global package upgrades and routine
cleanup as part of this change. A rollback to a vulnerable runtime is a temporary
recovery state, not approval to resume internet-facing workloads.

OS rollback is different. The [OCLP FAQ](https://dortania.github.io/OpenCore-Legacy-Patcher/FAQ.html#can-i-downgrade-macos-while-keeping-data)
explains that direct macOS downgrades are unavailable and reverting requires an
erase/restore path. That destructive operation needs its own owner-approved scope;
there is deliberately no remote erase or reinstall command here. If the machine
does not boot or patches fail, stop dispatch and use the rehearsed recovery path.

Fill in [runtime-update-plan.json](../examples/runtime-update-plan.json) with exact
approved versions and evidence. It is a non-executable review artifact, not a
provisioning API. Acceptance requires dated before/after inventories, selected
artifact provenance, tested runtime/dependency compatibility, an exit status and
checksum-verified result from the actual worker. Record unsupported components
explicitly instead of labelling the whole machine “secure.”

## Accounts and public-web jobs

Use a dedicated **standard** macOS account with no personal browser sessions,
cloud credentials, developer signing identities or passwordless administrator
access. Limit Remote Login to the intended account, retain SSH host-key checking,
and keep agent forwarding disabled. Jobs inherit that account's permissions;
neither the workspace directory nor the queue is an OS sandbox.

Public-web jobs should carry approved domains, page/byte/time limits and a modest
per-origin request rate. Respect robots rules, site terms and server backoff; stop
on authentication walls, CAPTCHA or access denial. The worker naturally uses its
actual home-network route. Do not add proxies, identity spoofing or bypass logic.
Content fetched from the web is untrusted data, never instructions granting access
to local files or authority to run commands. See [SECURITY.md](../SECURITY.md).
