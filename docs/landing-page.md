# MacMinionFabric landing page

The public landing page lives in `site/` and is served by Cloudflare Pages. It uses
plain HTML, CSS, a local SVG favicon, and system fonts. There are no JavaScript
dependencies, hosted fonts, analytics, or external assets. All outbound links
point to this GitHub repository.

The visual direction is the minimalist web of around 2004: compact Verdana type,
blue underlined links, square corners, a narrow page, and a small sidebar. The
layout adapts to mobile screens. This page has its own light palette; the private
activity dashboard keeps its existing design.

Preview from the repository root:

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory site
```

Open `http://127.0.0.1:8000`. Check desktop and mobile layouts, keyboard focus,
navigation, and the pairing/documentation links after changes.

Cloudflare Pages project `macbookminion` connects to this repository's `main`
branch. Its build command is `bash scripts/verify-cloudflare-build.sh`, its
output directory is `site`, and its root directory is `/`. It checks local page
assets before publishing the static files. Pushes to `main` start the build.
The controller's macOS test suite still runs locally with
`python3 -m unittest discover -s tests -v`. The live address is
<https://macbookminion.pages.dev/>. GitHub Actions is no longer used for this
repository.
