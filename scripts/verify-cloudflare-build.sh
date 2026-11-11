#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python3 -m unittest discover -s tests -v

for script in skills/macbook-minion/scripts/*.sh examples/*.sh; do
  /bin/bash -n "$script"
done
