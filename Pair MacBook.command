#!/bin/bash
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
printf 'MacBook Minion pairing\n\n'
printf 'Enter the old Mac address shown under Sharing > Remote Login\n'
printf '(e.g. username@hostname.local): '
read -r target
/bin/bash "$base/skills/macbook-minion/scripts/pair.sh" "$target"
result=$?
if [ "$result" -ne 0 ]; then
  printf '\nSetup stopped before completion. Tell Codex the last error above.\n'
fi
printf '\nPress Return when you are ready to close this window.\n'
read -r answer
exit "$result"
