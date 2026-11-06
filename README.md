# Manual Beagle

[![CI](https://github.com/manualbeagle/manualbeagle/actions/workflows/ci.yml/badge.svg)](https://github.com/manualbeagle/manualbeagle/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Buy me a coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-stevenstorie-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/stevenstorie)

Give it the brand and model from an appliance's rating plate. It finds the maker's own manual, makes every page
searchable, and files it in the inventory you already use: [Homebox](docs/integrations/homebox.md) or
[a plain folder](docs/integrations/folder.md), with more to come. Anyone can add support for a brand, and every
brand added makes it find more.

**Status:** early development (0.x). It works, but expect changes.

- **Brand adapters** in the style of yt-dlp's extractors: mostly short YAML files, with a Python escape hatch,
  and test cases that run against the live sites. [See which brands work.](docs/brands.md)
- **A web-search fallback** (your own SearXNG) when no adapter knows the brand.
- **Searchable to the page:** text from every page, OCR only where a PDF has none, and full-text search.
- **Ask your manuals:** a question in your own words, answered from your manuals only, citing the pages; with an
  optional model (your own through Ollama, or Claude), or as the best pages without one.
- **A CLI, a web UI with an HTTP API, and an MCP server** for AI assistants. The web UI runs on your own
  computer: [docs/web.md](docs/web.md).
- **Scan a rating plate** with your phone: OCR reads the brand and model, you check them, and Find runs. An
  optional vision model (your own through Ollama, or Claude) can help with hard-to-read plates.
- **No AI required.** Everything works without it.

Manual Beagle fetches manuals for the person who owns the product, on their own machine. It never hosts or mirrors
PDFs, and it never gets around a site's bot protection or login. [What it sends where.](docs/privacy.md)

## Quickstart

You need Python 3.12 or newer. Install it from [PyPI](https://pypi.org/project/manualbeagle/) into a virtual
environment:

```
python -m venv .venv
.venv/bin/pip install manualbeagle        # Windows: .venv\Scripts\pip install manualbeagle
```

To work on the code instead, install it from a clone: [CONTRIBUTING.md](CONTRIBUTING.md#set-up).

Then, with the virtual environment active (`source .venv/bin/activate`, or `.venv\Scripts\activate` on Windows):

```
manualbeagle adapters list                                       # the brands it knows
manualbeagle find LG WM5700HVA
manualbeagle find "Mitsubishi Electric" MSZ-FH12NA --fetch      # download the best result and check its text
manualbeagle find Toyota "2022 Sienna"
manualbeagle search "door seal"                                  # every page of every downloaded manual
manualbeagle sync homebox --dry-run                              # what it would attach to your Homebox items
manualbeagle batch my-things.csv                                 # brand,model rows: the best manual for each
manualbeagle batch my-things.csv --fetch                         # ...and download them; coverage by brand
manualbeagle batch my-things.csv --fetch --export ~/Manuals      # ...and copy them to a folder, with an index
manualbeagle export ~/Manuals                                    # the library as Brand/Model - Kind.pdf
manualbeagle ocr                                                 # OCR manuals fetched before OCR was installed
manualbeagle nameplate plate.jpg                                 # read a rating plate photo (needs Tesseract)
manualbeagle ask "how do I clean the lint filter?"               # answer from the library, citing pages
manualbeagle serve --mcp                                         # for an AI assistant, over stdio
```

### The web UI

The same lookups, the library's page search and reader, batch lists and Homebox sync, in your browser. It
listens on 127.0.0.1 only, unless you ask otherwise ([the web UI](docs/web.md)):

```
.venv/bin/pip install "manualbeagle[web]"   # Windows: .venv\Scripts\pip install "manualbeagle[web]"
manualbeagle serve --web --open        # http://127.0.0.1:8765/
```

On a phone, **Scan a rating plate** reads the brand and model from a photo. To install it as an app, serve it
over HTTPS behind a reverse proxy ([on a phone](docs/web.md#on-a-phone-https)).

`find` prints the ranked candidates, each with its score and kind; `★` marks the best, and a result marked
`[link only: open it yourself]` is a page for a person to open rather than a file to download. `--why` shows how
each score was made.

The web-search fallback asks a [SearXNG](https://docs.searxng.org) at `http://localhost:8080` by default. If you
don't run one, `find` says `web search: ✗` and shows what the brand's adapters found, so brands without an
adapter find nothing. Run SearXNG with its JSON API turned on and point `[search] url` at it in the config
([configuration](docs/architecture.md#configuration)), or pass `--no-search` to skip it.

### In Docker

One image has the web UI, the CLI, the MCP server and OCR built in, for linux/amd64 and linux/arm64:
`ghcr.io/manualbeagle/manualbeagle`. [Running it in Docker](docs/docker.md) shows how to run it, or build it from
the [Dockerfile](Dockerfile).

### OCR for scanned manuals (optional)

Some manuals are scans with no text, so search can't see them. To OCR those pages, install
[Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html) and OCRmyPDF into the same environment
(`pip install ocrmypdf`). Manual Beagle finds `ocrmypdf` beside its own Python, and on Windows finds Tesseract in
its installer's folder even when it isn't on PATH. It writes plain PDFs, so Ghostscript isn't needed. New downloads
are OCRed as they arrive; `manualbeagle ocr` does the ones already in the library. Reading rating plate photos
needs Tesseract alone.

### Into Homebox

Make an API key on your Homebox profile page, then:

```
export HOMEBOX_TOKEN=...                                 # PowerShell: $env:HOMEBOX_TOKEN = "..."
export MANUALBEAGLE_HOMEBOX_URL=http://homebox.example:7745
manualbeagle homebox status
manualbeagle sync homebox --dry-run --report sync.json
manualbeagle sync homebox
```

The [Homebox guide](docs/integrations/homebox.md) explains what it uploads and what it links.

Manuals also say how often to clean, check or replace things. `manualbeagle maintenance suggest` reads them for
those tasks (rules only; `--ai` asks an optional model too), and the web UI's **Maintenance** page lets you
approve each one as a scheduled Homebox maintenance entry, with the page it came from.

## Integrations

| Where | What it does | Guide |
|---|---|---|
| Homebox | Reads items with a manufacturer and model; attaches the PDF, or the maker's link. Can also seed Homebox from a list, add a scanned appliance with its plate photo, and run every night. | [homebox.md](docs/integrations/homebox.md) |
| A plain folder | `batch` from a YAML or CSV list, `Brand/Model - Kind.pdf` files and an index. | [folder.md](docs/integrations/folder.md) |
| Your browser | Find and fetch, scan a rating plate (and add the appliance to Homebox), search and read the library page by page, run lists, sync Homebox (a dry run, then attach). On this computer only, by default; on a phone through your own HTTPS proxy. | [web.md](docs/web.md) |
| AI assistants (MCP) | Find manuals, search and read them page by page; downloads only with `--allow-write`. | [mcp.md](docs/integrations/mcp.md) |
| Home Assistant | Reads its devices (maker, model, name, area) to find their manuals and offer each as a new Homebox item in its area; read only, and nothing is added without a confirm. | [homeassistant.md](docs/integrations/homeassistant.md) |
| Paperless-ngx | Files the library's manuals with brand and model tags and the Manual type, never twice; by hand or every new one. | [paperless.md](docs/integrations/paperless.md) |
| Grocy | Planned. | |

## Docs

- [How it works](docs/architecture.md), including configuration
- [The web UI and its HTTP API](docs/web.md)
- [Brands and their status](docs/brands.md)
- [Writing a brand adapter](docs/adapters.md)
- [Privacy](docs/privacy.md)
- [Running it in Docker](docs/docker.md)
- [Releasing](docs/releasing.md), for maintainers
- [Changelog](CHANGELOG.md)

## Contributing

The most useful contribution is a brand adapter for something you own: usually a short YAML file and about 15
minutes. [CONTRIBUTING.md](CONTRIBUTING.md) walks through one, and [docs/brands.md](docs/brands.md) shows which
brands need help. Please read the [Code of Conduct](CODE_OF_CONDUCT.md); report security problems as
[SECURITY.md](SECURITY.md) describes, not in an issue.

## License

Copyright 2026 Steven Storie. Licensed under the [Apache License 2.0](LICENSE); see also [NOTICE](NOTICE).

Made by **StorieDev**. Manual Beagle is free and stays free; if it found a manual you needed, you can
[buy me a coffee](https://buymeacoffee.com/stevenstorie). ☕
