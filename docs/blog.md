# Giving an old Intel Mac a useful job in my Codex workflow

I want the machine I am working on to stay responsive while the slower, independent
work happens somewhere else. An older Intel Mac is a useful place to put a file
conversion, a compatible test run, or a small public-web research job. I do not
need it to be a fast new laptop. I need to know what it can run, what it is doing,
and whether the result actually came back intact.

MacBook Minion is my way of making that arrangement repeatable. Codex stays on the
main machine as the control plane. A small Python controller sends approved files
over SSH, puts jobs in a shared persistent queue and retrieves results with
checksum verification. The worker can run ordinary shell jobs with the tools
already available on macOS; it does not need its own Codex installation.

The part I care about most is the handoff. Every job has an owner, a title and a
description. The queue keeps the job ID when a chat closes. A local dashboard shows
progress and output, and the worker has one job lock so separate Codex sessions do
not pile work onto the same laptop. A saved queue receipt means the request exists.
Completion means I have inspected the exit status and fetched the actual output.

That distinction helps with interrupted connections too. If a reply disappears,
I want to reconcile the existing job before considering another run. A conversion
can be expensive to duplicate. A browser action can have consequences beyond the
machine. SSH access does not grant a job permission to send messages, log into
accounts or change someone else's service.

## Four different kinds of pressure

Offloading gives a job access to another machine's CPU and memory. It does not pool
RAM across laptops or make every build faster. Architecture, dependency support,
transfer time and the job's size determine whether it is worth moving. I start
with a bounded example and measure the complete round trip before making a speed
claim.

Disk space needs the same honesty. Worker scratch can hold reproducible intermediate
files, but copying a project does not automatically reclaim space on the main Mac.
The source remains where it was. The result has to be verified, and any cleanup
needs to follow the owner's existing policy. This machine is a worker, not a backup.

Token pressure is different again. Moving a command to an Intel Mac does not move
model inference there and does not make that command's reasoning free. I can keep
logs and intermediate artifacts out of the conversation and return a compact,
structured result with the evidence needed to check it. HaiKrew can divide the work
into bounded tasks, with Codex integrating and reviewing the result. I have no
measured token-saving figure to attach to that approach yet.

The controller now chooses a compatible worker using a task's requirements and
fresh capacity information, while preserving the same ownership and result checks.
During verification it observed high load, warning memory pressure and low free
disk on the primary Mac, selected the idle Intel worker, and queued the project's
own tests. The initial 95 behavioral checks passed on that Mac, along with Bash syntax
checks and the skill validator. I fetched and checksum-verified the reports.

I have one extra MacBook. Multiworker registration and selection are covered by
tests, but I am not presenting a simulated fleet as several running computers.
The live result is one Intel machine doing useful work while Codex retains task
ownership. The router also makes uncertainty visible: subscription usage was
unknown in this run, so it requested compact results rather than inventing a
remaining-token figure.

## The browser really runs at home

A public-web browser job runs on the worker and uses that machine's actual network
route. On a normal home connection, that means the household's ordinary internet
egress. If a VPN or other routing configuration is present, that changes the route;
I should inspect it instead of assuming an address.

There is no reason to disguise that arrangement. I am not setting up rotating
proxies, copied personal profiles or anti-bot bypasses. I want a small, clear brief:
these public pages, this output, this deadline. A crawler should have explicit
domain, page and byte limits, obey robots rules and site terms, pace requests and
stop when the server asks it to. A login wall or CAPTCHA is a boundary, not an
invitation to make the browser more evasive.

The HTTP collector now handles ordinary text pages without launching a browser.
It restricts seed origins, checks robots.txt, pins public-IP connections, caps
each response at 1 MiB and stops on access or rate limits. Redirects are recorded
for review rather than followed. This is a small research primitive; choosing
good sources and judging their evidence remains the primary agent's job.

The headless adapter uses a fresh browser profile and returns artifacts
such as text and screenshots. That is useful evidence, but it is not my signed-in
desktop browser. The worker account also deserves care: the job runs with that
account's permissions. A dedicated standard account with no personal credentials
is a much better starting point than treating a scratch folder as a sandbox.

## Keeping the old Mac current takes judgment

OpenCore Legacy Patcher is part of the maintenance plan for older Macs, but I do
not want a background job deciding to change a bootloader and restart a machine.
The plan needs an exact model and target OS, a verified external backup, tested
recovery media and someone at the keyboard. Root patching can affect system
protections, and OS updates can require patches to be reapplied. Those tradeoffs
belong in an attended maintenance window. [OCLP's post-install guidance](https://dortania.github.io/OpenCore-Legacy-Patcher/POST-INSTALL.html)
explains why.

Runtime support does not all move together. Python's [macOS installers](https://docs.python.org/3.15/using/mac.html)
still cover Intel through universal2 builds. Node's [supported-platform table](https://github.com/nodejs/node/blob/v24.x/BUILDING.md)
sets a minimum OS for its current LTS binaries. Ruby publishes a [maintenance
schedule](https://www.ruby-lang.org/en/downloads/branches/) so I can distinguish a
working old interpreter from a maintained one. I record those requirements and
test a new isolated environment before changing the jobs that depend on it.

PyTorch is a particularly useful reality check. The [official wheel index](https://download.pytorch.org/whl/cpu/torch/)
has current Linux x86_64 CPU wheels, while current macOS wheels target Apple Silicon.
Installing a newer macOS with OCLP does not change an Intel CPU into an ARM CPU. For
current official PyTorch on this hardware, a maintained Linux installation is the
route I would evaluate. Calling an old Intel macOS package “latest” would hide the
actual maintenance problem.

The [Intel worker guide](intel-workers.md), read-only runtime inventory and
[concrete runtime plan](runtime-maintenance-options.md) make those choices explicit.
The current inventory still shows Monterey 12.7.6, Python 3.13.2, system Ruby 2.6,
and no Node or PyTorch. The OCLP application is present, but that alone does not
prove its bootloader or root patches are active. I am preparing maintenance for
an attended window; publishing a controller does not mean the worker was upgraded.

The project is [open source under MIT](https://github.com/ashrocket/macbookminion).
Its controller and HTTP collector use Python's standard library, and the
repository includes the skill, queue, pressure policy, tests and maintenance notes.

The result I am aiming for is ordinary and valuable: a main laptop that remains
pleasant to use, old hardware with a clear purpose, and background work I can
account for without filling the conversation with its entire execution history.
