# MacMinionFabric landing page

The public landing page lives in `site/` and is served by GitHub Pages. It uses
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

In repository Settings → Pages, the publishing source is GitHub Actions.
`.github/workflows/pages.yml` uploads only `site/`, then deploys it on changes to
that directory or the workflow on `main`. The workflow also supports a manual
run on `main`. It uses GitHub's official actions pinned to commit hashes.

GitHub's [custom workflow guide](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
documents the Pages deployment setup.
