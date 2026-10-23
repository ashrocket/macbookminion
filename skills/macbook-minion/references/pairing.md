# Pair the worker

Start over the same Wi-Fi/LAN. Ethernet is useful for large transfers and stability;
it uses exactly the same SSH workflow. The 2015 15-inch MacBook Pro has 802.11ac,
USB 3, and Thunderbolt 2. Use a compatible Ethernet adapter if needed. An ordinary
USB charging/data cable between the Macs is not this network connection. No port
forwarding or public SSH exposure is needed.

## On the older Mac

1. Plug it into power and keep the lid open. Connect it to the same trusted network.
2. For separation from personal files, create a dedicated **standard** account such
   as `minion` in Users & Groups. An existing user works but grants that account's
   normal access. Do not give the worker administrator privileges just for this.
3. On Monterey and earlier: System Preferences → Sharing → Remote Login. Enable it
   for **Only these users** and select the intended account. Full disk access is
   unnecessary for a workspace in that account's home directory. On newer macOS:
   System Settings → General → Sharing → Remote Login.
4. Copy the displayed `ssh username@hostname` line. Also run in its Terminal:

   ```sh
   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```

   Compare that SHA256 fingerprint with the prompt on the controlling Mac. If that
   host-key file is absent, use the displayed key type's `.pub` file instead.

## On the controlling Mac, in Terminal

For a guided setup after obtaining the address, run `bash <skill>/scripts/pair.sh
USER@HOST` in a real Terminal window. It explains the passphrase and login prompts,
creates a dedicated key if absent, installs only the public key, then verifies and
saves the pairing. It refuses to replace an existing pairing. Codex can open this
guided setup for the user; the user enters passwords and accepts host trust in
Terminal. The manual steps below provide the same workflow when preferred.

Replace `minion@older-mac.local` below with the real address. These interactive
steps keep passwords and private keys out of chat. Skip key creation if an
appropriate dedicated key already exists; never overwrite an existing key.

```sh
ssh-keygen -t ed25519 -f ~/.ssh/macbook-minion -C macbook-minion
ssh-add ~/.ssh/macbook-minion
ssh -o ForwardAgent=no -o StrictHostKeyChecking=ask minion@older-mac.local true
ssh-copy-id -i ~/.ssh/macbook-minion.pub minion@older-mac.local
```

Choose a key passphrase, and verify the host fingerprint before accepting the
first connection. `ssh-copy-id` installs only the public key. The Mac's login
password is entered into Terminal, never into Codex. Subsequent automation uses
batch mode with strict host checks. Unlock the key again with `ssh-add` if the
agent has forgotten it. The controller never forwards the SSH agent to the worker.

From the MacBook Minion repository:

```sh
./bin/minion probe --host minion@older-mac.local --identity ~/.ssh/macbook-minion
./bin/minion configure --host minion@older-mac.local --identity ~/.ssh/macbook-minion
./bin/minion init
./bin/minion plan examples/smoke-input
./bin/minion put examples/smoke-input
./bin/minion run SNAPSHOT_ID --script examples/smoke-job.sh
./bin/minion status JOB_ID
./bin/minion logs JOB_ID
./bin/minion fetch JOB_ID results/first-smoke
```

When using the installed skill, replace `./bin/minion` with
`python3 ~/.agents/skills/macbook-minion/scripts/minion.py`.

`configure` tests SSH and writes only local metadata. `init` creates the remote
workspace. `put` and `run` are the first file-transfer/job actions. Configuration
defaults to `~/.config/macbookminion/config.json`; use `--config PATH` before the
subcommand for an alternative. No shell profile or SSH configuration is rewritten.

## Stop everything, from the old Mac

`init` also installs a local, offline kill switch on the worker itself:
`~/.macbookminion/kill-switch.sh`, and a double-click
`~/Desktop/Stop-MacBook-Minion.command`. Run it any time you are sitting at the
old Mac to force-stop anything MacBook Minion left running there right now. It
needs no network access and does not depend on the controller or the
pairing in any way.

It does not block new connections by itself. The actual guarantee that
nothing new can start is turning off Remote Login: System Settings > General
> Sharing > Remote Login. With the listener off, the controller has nothing
to connect to. Turn it back on later to use this worker again; the existing
pairing still works, no re-pairing needed.

## Troubleshooting and recovery

- **Timeout:** check power, lid, Remote Login, address, and network isolation. A
  guest Wi-Fi network may prevent device-to-device communication. A `.local` name
  can fail across networks; use the displayed IP or an SSH alias.
- **Permission denied:** check the selected Remote Login user, public-key install,
  and `ssh-add -l`. Never print private key files or copy an agent socket.
- **Host key changed:** compare fingerprints on the older Mac before updating the
  specific known-host entry. Never set `StrictHostKeyChecking=no`.
- **Job interrupted:** preserve the job ID, inspect `status`, `logs`, and processes.
  Jobs survive an SSH disconnect via nohup, but not shutdown, reboot, or every
  account logout policy. A stale `~/.macbookminion/active` lock blocks new jobs.
  Only after proving that the old runner and its children have stopped, remove
  its `active/job` marker and empty lock directory. There is no automatic retry or
  PID-based kill command, to avoid duplicated work or killing a reused PID.
- **Disk low:** inspect space and completed snapshots/jobs; remove only explicit
  disposable IDs after result retrieval. The tool never deletes local inputs.
  The floor is checked before writes, not continuously enforced during jobs.
- **New network:** update the existing config deliberately or use a separate
  `--config` file. For access away from home, design a private VPN separately.

First live acceptance: verify Intel architecture and OS, copy the smoke input,
run the smoke script, observe exit 0, fetch and verify its SHA-256 manifest, and
inspect the returned result. Then measure a representative real task before
claiming it makes work faster.

Sources checked 2026-09-24:
- [Apple Remote Login, Monterey](https://support.apple.com/guide/mac-help/mchlp1066/12.0/mac/12.0)
- [Apple 2015 15-inch specifications](https://support.apple.com/en-us/111955)
- [OpenAI local skill discovery](https://learn.chatgpt.com/docs/build-skills)
