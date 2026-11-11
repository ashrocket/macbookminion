#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python3 - <<'PY'
from html.parser import HTMLParser
from pathlib import Path

site = Path('site').resolve()
index = site / 'index.html'
if not index.is_file() or not index.read_text().strip():
    raise SystemExit('Missing or empty site/index.html')

class LocalAssets(HTMLParser):
    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key not in {'href', 'src'} or not value:
                continue
            if value.startswith(('https:', 'http:', 'mailto:', '#')):
                continue
            relative = value.split('?', 1)[0].split('#', 1)[0]
            if relative in {'', './'}:
                continue
            asset = (site / relative).resolve()
            if site not in asset.parents or not asset.is_file():
                raise SystemExit(f'Missing local asset: {relative}')

LocalAssets().feed(index.read_text())
print('Static site assets verified')
PY
