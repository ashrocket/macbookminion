#!/bin/bash
set -euo pipefail
test -r input.txt
{
  printf 'MacBook Minion completed on '
  uname -sm
  printf '\nInput checksum:\n'
  shasum -a 256 input.txt
  printf '\nInput:\n'
  cat input.txt
} > "$MINION_OUTPUT_DIR/result.txt"
printf 'Smoke job complete.\n'

