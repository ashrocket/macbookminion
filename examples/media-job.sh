#!/bin/bash
# A small real FFmpeg round trip with synthetic input; no private media required.
set -euo pipefail
test -r input.txt
ffmpeg -hide_banner -nostdin -loglevel error \
  -f lavfi -i 'sine=frequency=440:duration=5' \
  -c:a aac -b:a 96k "$MINION_OUTPUT_DIR/tone.m4a"
ffmpeg -hide_banner -nostdin -loglevel error \
  -i "$MINION_OUTPUT_DIR/tone.m4a" -f null -
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import platform

output = Path(os.environ["MINION_OUTPUT_DIR"])
audio = output / "tone.m4a"
report = {
    "result": "generated and decoded successfully",
    "architecture": platform.machine(),
    "python": platform.python_version(),
    "audio_bytes": audio.stat().st_size,
    "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
}
(output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
PY
