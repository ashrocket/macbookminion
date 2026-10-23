#!/bin/bash
# Run in a real Terminal so passwords and passphrases never enter chat/tool logs.
set -euo pipefail
umask 077
scripts_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
target=${1:-}
if [[ ! "$target" =~ ^([A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*$ ]]; then
  printf 'Usage: bash pair.sh username@hostname\n' >&2
  exit 1
fi
if [ ! -t 0 ] || [ ! -t 1 ]; then
  printf 'Open this setup in Terminal. Passwords must not be entered into chat.\n' >&2
  exit 1
fi
key_path="$HOME/.ssh/macbook-minion"
config_path="$HOME/.config/macbookminion/config.json"
if [ -e "$config_path" ]; then
  printf 'A pairing already exists. Ask Codex to inspect it before changing it.\n'
  exit 1
fi
printf '\nMacBook Minion: connect to %s\n\n' "$target"
printf 'This setup creates a dedicated SSH key on this Mac and installs its public\n'
printf 'half on the old Mac. Your private key stays on this Mac.\n\n'
mkdir -p "$HOME/.ssh"
if [ ! -e "$key_path" ]; then
  if [ -e "$key_path.pub" ]; then
    printf 'A public key already exists without its private key; ask Codex to inspect it.\n' >&2
    exit 1
  fi
  printf 'First, choose a passphrase to protect the new connection key.\n'
  printf 'Terminal hides characters while you type. Enter it twice when asked.\n\n'
  /usr/bin/ssh-keygen -t ed25519 -f "$key_path" -C macbook-minion
fi
printf '\nLoading the connection key. If it has a passphrase, Terminal will ask for it.\n\n'
/usr/bin/ssh-add "$key_path"
printf '\nNext, sign in using the OLD MacBook user password.\n'
printf 'If asked to trust the computer, verify its identity before answering yes.\n'
printf 'The public-key fingerprint can be checked on the old Mac.\n\n'
/usr/bin/ssh-copy-id -i "$key_path.pub" -o ForwardAgent=no -o StrictHostKeyChecking=ask "$target"
printf '\nChecking the connection and reading the old Mac hardware details...\n\n'
python3 "$scripts_dir/minion.py" configure --host "$target" --identity "$key_path"
python3 "$scripts_dir/minion.py" init
printf '\nPairing finished. Return to Codex and say: The Terminal setup finished.\n'
