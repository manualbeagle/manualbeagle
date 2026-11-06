# Changelog

All notable changes to Manual Beagle are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x,
anything may change between minor versions.

## [Unreleased]

## [0.1.0] - 2026-11-05

The first release, on PyPI (`pip install manualbeagle`) and as the image `ghcr.io/manualbeagle/manualbeagle`.

### Added

- **The nightly live run reports breakage:** `.github/workflows/live-adapters.yml` runs every adapter's cases
  against the live sites each night once the repository is public (Mondays while it's private), shows a live
  scoreboard and a report of what broke on the run's page, and keeps one issue labelled `live-adapters` open while
  anything is broken, closing it when everything passes. New commands behind it: `adapters test --json FILE`
  (every case's result as JSON), `adapters scoreboard --live FILE` (brands.md with a Live column) and
  `adapters report FILE` (the broken cases as Markdown; exit 1 if any). CONTRIBUTING.md says how to pick up a
  broken adapter, and has a section on changes other than adapters.
- **Maintenance suggestions read from manuals:** `manualbeagle maintenance suggest` reads every manual in the
  library for maintenance tasks with an interval of a week to ten years ("clean the filter every 2 weeks",
  "replace the hoses every five years"), by rules: sentences, bullets under a part's name, interval headings
  ("EVERY MONTH"), frequency tables and schedule rows. Each keeps its page and a quote and is matched to Homebox
  items by brand alias and model. With `--ai` (or **Also ask** on the page) the `[ai]` model may reword or add
  tasks, kept only when its quote is on the page and its interval is one the page states. The web UI's new
  **Maintenance** page lists them; **✓ Add to Homebox** files one as a Homebox maintenance entry with a scheduled
  date (`POST /api/v1/entities/{id}/maintenance`), **✗ Dismiss** keeps it from being suggested again. Nothing
  reaches Homebox without that click. Suggestions and decisions live in the library's `maintenance` table.
  Also `maintenance list`, the MCP tool `maintenance_suggestions` (read-only) and `/api/maintenance`.
  Documented in `docs/web.md` and `docs/integrations/homebox.md`.
- **Home Assistant as a source:** `manualbeagle ha status|devices|import` reads Home Assistant's device
  registry over its WebSocket API (list commands only, so a read-only user's token works) and offers each device
  Homebox doesn't have as a new item, with its area as the location. Services (the sun, backups, add-ons),
  helpers, disabled devices, devices with no maker or model, and a second device of the same model are skipped,
  each with the reason; one Homebox has with the same maker and model shows as `✓ have`. `--find` looks up each
  device's manual. Nothing is written without a confirm (`[y]es`, `[n]o`, `[e]ach`, or `--yes` after a dry run).
  In the web UI under **Homebox** (`GET /api/homeassistant/devices`, `POST /api/homeassistant/import`). The
  `[home_assistant]` table (`url`, `token_env` = `HA_TOKEN`, `skip_integrations`) and `MANUALBEAGLE_HA_URL`. New
  dependency: `websockets` (BSD-3-Clause). Documented in `docs/integrations/homeassistant.md`.
- **A Paperless-ngx sink:** `manualbeagle paperless status|send [MATCH]` files the library's manuals with a title,
  brand and model tags, a `manual` tag and the Manual document type (made with matching None when missing),
  after checking Paperless hasn't the file already by its checksum (SHA-256 for Paperless 3, MD5 for 2), and
  remembers what it sent (the `paperless` table). `[paperless] auto = true` files every new manual by itself,
  after a downloading command and from the web UI's background thread. In the web UI on the Library page and the
  reader (`GET /api/paperless`, `POST /api/paperless/send`). Documented in `docs/integrations/paperless.md`.
- **Settings page:** Home Assistant's and Paperless-ngx's addresses, Paperless's `auto` and document type. Their
  tokens are bound to the address set beside them in config.toml or the environment, so an address changed on
  the page alone never receives a token. The same now holds for Homebox's API key (`sync.homebox()` refuses an
  address the page alone set). `config.TokenSource` holds the key fields every table shares.
- **Ask your manuals:** a question in your own words ("how do I clean the lint filter?") answered from the
  library's own pages, citing each page it used; each citation opens the Reader at that page. Retrieval is the
  per-page index (the question's words, narrowed to a brand, model or document when one is picked or named);
  the `[ai]` model (Ollama or Claude) writes a short answer from the best four pages and says "The manuals don't
  say" rather than guess. With no model it returns the best pages and their snippets. In the web UI (**Ask**,
  `POST /api/ask`), on the command line (`manualbeagle ask "..."`) and over MCP (`ask_manuals`, read-only).
  `Library.search` takes a `document`. Documented in `docs/web.md`, `docs/architecture.md`,
  `docs/integrations/mcp.md` and `docs/privacy.md`.
- **Add to Homebox** after a rating-plate scan in the web UI: once the brand and model are confirmed, it suggests
  a name (brand, kind of product, model), the location comes from Homebox's list, and an item that already has
  the same maker and model is offered first so nothing is duplicated. After a review of every write, it creates
  the item with its manufacturer, model and serial number, attaches the plate photo as its picture, and attaches
  the manual by `sync homebox`'s rule. `POST /api/homebox/new-item/preview` (reads only) and
  `POST /api/homebox/new-item` (needs `confirm: true`). Documented in `docs/web.md` and
  `docs/integrations/homebox.md`.
- **`sync homebox --metrics FILE`** writes the run's outcome as a Prometheus textfile for node_exporter (when it
  ran, success, items by decision, manuals attached, writes refused), also when Homebox can't be reached, so a
  nightly run can be watched. `docs/integrations/homebox.md` has a systemd timer to run it every night.
- **A Settings page** in the web UI: the delay, robots.txt, web search, the Homebox address and links, and the
  vision model (with a list of the models an Ollama has), each showing where its value comes from (`✎ changed
  here`, `● config file`, `○ default`, `▲ set by the server's environment`). Changes are kept in `settings.json`
  beside the library, win over config.toml and are used at once; **Use the file's value** takes one away. Where
  keys are read from, `home` and `adapter_dirs` stay in the file. Documented in `docs/web.md`.
- **Rating plates:** photos are greyed before OCR (Pillow, in the `web` extra), which reads coloured plates
  far better, and an unlabelled model guess is never a phone number, a speed or a postcode. When OCR misses the
  brand or model and the vision model runs on your own network (Ollama), the page asks it without a click; the
  model's answer is trimmed to the model number.
- **Better links from web search:** a URL whose path names another language (`/us/es/`) or country (`/hk_en/`)
  ranks lower, a maker's staging or test server (`support.dev1.…`) ranks far lower, and tracking parameters
  (`utm_*`, click IDs such as `msockid`) are dropped from every result's URL. `Query.region` (default `us`)
  is the country preferred.
- **Rating plate scanning** in the web UI: **Scan a rating plate** takes a photo (`capture="environment"`), and
  **Choose a photo** picks one from the phone's photo library; the server reads it with Tesseract and suggests the brand, model and serial from the plate's labels (MODEL,
  M/N, MOD. NO., S/N, SERIAL) and the adapters' brand names, and the person confirms before Find runs. Photos are
  kept in the library (metadata stripped) and listed on the Library page. `POST /api/nameplate` and
  `/api/photos`; `manualbeagle nameplate PHOTO` on the command line. An optional vision model adds its own
  reading on request: Ollama (`gemma3` by default) or Claude, set in `[ai]`. Documented in `docs/web.md`.
- **The web UI on a phone:** behind `serve --web --public-url`, a year-long token cookie, a sign-in page for
  installed apps, and a web app manifest, icons and service worker so the page installs as an app.
- **Web UI and HTTP API** (`manualbeagle serve --web`, the optional `web` extra): find and fetch, per-page
  library search with a page reader and the PDF in the browser, the document list, batch lists (with or without
  downloading) and Homebox sync as a dry run followed by a confirmed attach, with background jobs that can be
  cancelled. Plain HTML, CSS and JavaScript on Starlette; every status is a glyph and a word. Listens on
  127.0.0.1 and answers only loopback Host names; changes must be same-origin JSON; `--host` beyond loopback
  needs a token, and so does `--public-url` (behind a TLS reverse proxy). The page's Fetch refuses manual
  mirrors, as MCP `fetch_manual` does.
  Documented in `docs/web.md`.
- **Lookup pipeline** (`manualbeagle find BRAND MODEL`): brand aliases and model variants, the brand's adapters,
  the maker's support page as a link, a web-search fallback, and rules-only ranking with `--why` to explain each
  score. `--fetch` downloads the best result and checks that its text names the model; `--json` for scripts.
- **Brand adapters** in YAML (json, html and regex search steps, templates, filters, kinds) with a Python escape
  hatch, each with its own test cases. 34 brands: 21 that search the maker's site themselves, 6 partial or
  link-only, and 7 marked blocked because their sites refuse automated requests. Shared Python adapters for
  makers on Shopify stores and Zendesk help centres. `family_manuals` for makers whose manuals cover a range.
- **Adapter test harness:** `manualbeagle adapters test` replays recorded fixtures offline, runs live, or
  re-records (`--record`, with fixture trimming); `--check-text` downloads each hit to check its text. A weekly
  live run in GitHub Actions.
- **Scoreboard:** `manualbeagle adapters scoreboard` generates `docs/brands.md`, with how to help each status.
- **Polite HTTP client:** one request at a time per site with a delay, a User-Agent naming the project, a
  response size cap, refusal of private addresses and non-http schemes (checked on every redirect), and a stop
  at bot walls and CAPTCHAs. robots.txt is not applied to single lookups; `respect_robots` turns it on.
- **Web search** through SearXNG's JSON API, spaced out, stopping at the first query that finds a PDF naming the
  model, and saying so when the upstream engines are suspended.
- **Library:** content-addressed PDFs, per-page text with pypdfium2, OCR with OCRmyPDF for pages without text
  (when installed), and SQLite full-text search (`search`, `docs`, `fetch`). `manualbeagle ocr` OCRs documents
  fetched before OCR was installed. OCR finds `ocrmypdf` beside its own Python and Tesseract in its Windows
  install folder, and needs no Ghostscript.
- **Homebox:** `homebox status`, `homebox items`, `homebox seed FILE` (with `--dry-run`), and `sync homebox`
  (with `--dry-run`, `--report`, `--links-only`, `--force`, `--allow-search-links`), which uploads a PDF whose
  text names the model (or an adapter's own document for a related model or a family) and otherwise links the
  maker's page. `[homebox] attach_links = false` stops it attaching links. When this run's search finds no
  manual, one an earlier run downloaded for the same brand and model (its text naming the model) is used, so
  search's run-to-run differences don't lose a manual.
- **Lists and folders:** `batch FILE` for a YAML or CSV list, `batch --fetch` to download with sync's rule and
  print coverage by brand, `batch --fetch --export DIR` for `Brand/Model - Kind.pdf` files and an index, and
  `export DIR` for the whole library.
- **MCP server** (`serve --mcp`) with `find_manual`, `search_manuals`, `read_pages`, `list_documents`, and
  `fetch_manual` only with `--allow-write`. `fetch_manual` and the CLI's `fetch` refuse manual mirrors, on every
  redirect hop.
- **Configuration** in one TOML file; secrets from environment variables, files, or `token_command` (a
  password manager's CLI or a decrypting helper) for the Homebox key.
- **Documentation:** README, contributing guide, architecture, the adapter schema and Python API, integration
  guides for Homebox, folders and MCP, a privacy page, a security policy, a code of conduct, issue and pull
  request templates. Docstrings on all public code, checked by ruff.
- **Release preparation:** a Dockerfile (web UI, CLI, MCP and OCR in one image; nothing published yet) and
  [docs/docker.md](docs/docker.md); a release workflow that builds and checks the sdist, the wheel and the image
  and publishes to PyPI (trusted publishing) and the GitHub Container Registry only once it's switched on;
  [docs/releasing.md](docs/releasing.md); CI on Python 3.12, 3.13 and 3.14; the version written in one place
  (`src/manualbeagle/__init__.py`); a Buy Me a Coffee link (`.github/FUNDING.yml`, the README, the package's
  Funding URL). The release workflow runs on a pushed `v*` tag: it checks the tag against the version and
  CHANGELOG.md, installs both the wheel and the sdist in fresh environments, checks the image runs as an
  ordinary user, tags a final version's image `<major>.<minor>` and `latest` (not a pre-release's), and makes
  the GitHub release from the changelog after the PyPI upload. The image carries OCI labels (version, licence,
  source) and installs Tesseract before the code, so a code change rebuilds only the last layers; Dependabot
  watches its base image. docs/releasing.md has the local checks.
- **The fetching rule on site terms, restated:** a lookup that one person starts for one product's manual counts
  as browsing, so a maker's terms that ban scraping or automated collection in general don't rule out an
  adapter; its header notes the terms. Manual mirrors still get no adapter, and a maker that asks the project to
  stop gets a link-only brand. The Code of Conduct takes reports privately through the repository's Security
  tab, and the web UI's footer has a "☕ Support it" link. The design notes (`docs/design.html`) left the
  repository.

[Unreleased]: https://github.com/manualbeagle/manualbeagle/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/manualbeagle/manualbeagle/releases/tag/v0.1.0
