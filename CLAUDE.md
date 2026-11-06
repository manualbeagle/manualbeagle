# manualbeagle

Manual Beagle is an open-source (Apache-2.0) Python tool that finds appliance manuals by brand and model, makes
them searchable per page, and files them in Homebox and other inventories. It's a companion to an inventory app,
not one itself. Package and CLI: `manualbeagle`. Python 3.12+.

This file is for anyone working on the code with Claude Code. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the
walkthrough and [docs/architecture.md](docs/architecture.md) for how the pieces fit. A `CLAUDE.local.md` beside
this file, if you have one, holds your own notes and isn't committed.

## Layout

```
src/manualbeagle/
  cli.py            the command line (argparse); one cmd_* function per subcommand
  pipeline.py       find(): normalise, run adapters, web search, rank
  models.py         Query, Candidate, DocKind, FindResult
  normalize.py      brand aliases and model variants
  http.py           PoliteClient (all network access), Cassette (recorded fixtures)
  verify.py         rules-only scoring and ranking, PDF text checks
  ask.py            Ask your manuals: pick pages from the index, then an optional [ai] model answers, citing them
  library.py        downloads, per-page text (pypdfium2), OCR (OCRmyPDF), SQLite FTS5 index
  sync.py, batch.py sync homebox; batch FILE [--fetch]
  newitem.py        a scanned appliance into Homebox (the web UI's Add to Homebox)
  mcp_server.py     the MCP server (serve --mcp)
  config.py         config.toml; secrets from environment variables or files
  testing.py        the adapter test harness; scoreboard.py generates docs/brands.md
  adapters/         base.py (schema, Adapter), yaml_adapter.py, defs/*.yaml, py/*.py
  search/           web-search plugins (SearXNG)
  sinks/            Homebox, the plain folder, seeding Homebox, Paperless-ngx
  sources/          Home Assistant (read only: devices to offer as Homebox items)
tests/              pytest; tests/fixtures/<adapter>/<model>.json are recorded responses
docs/               architecture, adapters (schema), brands (generated), integrations, privacy
```

## Commands

```
pip install -e ".[dev]"
ruff check src tests                 # lint, including docstring rules (D) for src
ruff format src tests                # format; CI runs ruff format --check
pytest                               # offline: no network
pytest -m live -k <adapter>          # live tests against the real sites (slow; on demand)
manualbeagle adapters test <id> --record -v    # re-record an adapter's fixtures from the live site
manualbeagle adapters test <id>                # replay them offline
manualbeagle adapters scoreboard --write docs/brands.md
```

All of `ruff check src tests`, `ruff format --check src tests` and `pytest` must pass before a commit; check
their exit codes, not just the last line of output.

## Adding an adapter

1. Copy the closest `src/manualbeagle/adapters/defs/*.yaml`; the file name is the id. The schema is in
   [docs/adapters.md](docs/adapters.md).
2. Start the file with the header: the site, how it searches, known gaps, and `Last verified: <today>`.
3. If YAML can't express it, add `python: module:Class` and a class in `adapters/py/` that subclasses `Adapter`
   and overrides `find()` (and `trim_fixture()` for big pages). Check the shared `py/shopify.py` and
   `py/zendesk.py` first. The module docstring is the header; the class, `find` and `trim_fixture` need
   docstrings.
4. Add at least two real test cases, record fixtures with `--record -v`, check the fixture files are small and
   hold no PDF bodies, run the checks above, and regenerate `docs/brands.md`.

## Rules

- **Fetching.** Only on the user's machine, for that user; never host, cache for others, or mirror PDFs. Every
  request goes through `ctx.http` / `PoliteClient`: one request at a time per site, a delay between them, a
  User-Agent naming the project and its repository. Never get around CAPTCHAs, bot
  protection or logins (no browser automation, spoofed browser headers, copied cookies, proxies or retry loops);
  mark the brand `blocked` and fall back to web search.
- **Site terms: a lookup is browsing, not scraping.** One lookup that one person starts, for one product's
  manual, is treated like that person's browser, so an adapter may search a maker's own site even when its terms
  ban scraping, robots or automated collection in general; note the terms URL and what they say in the
  adapter's header. Still no adapter for manual mirrors such as ManualsLib (at most a link to their search
  page), and a brand becomes `link` as soon as its maker asks us to stop.
- **robots.txt doesn't apply to lookups.** Each lookup is one person asking for one product's manual, like a
  browser, not a crawler, so adapters may call paths robots.txt disallows, both while building an adapter and at
  run time; the `respect_robots` setting (off by default) turns the check back on. **Nothing that crawls or lists
  a site in bulk.**
- **No AI required.** Every feature has a rules-only path; AI plugins only add to it. Presets, defaults, docs and
  tests use only non-Chinese models: Claude, OpenAI, and Llama, Gemma or Mistral through Ollama. Never Qwen,
  DeepSeek, Yi, GLM, Kimi or similar.
- **Dependency licences** must be compatible with Apache-2.0: no PyMuPDF (AGPL). pypdfium2 for PDF text;
  OCRmyPDF plus Tesseract as an optional external tool.
- **Tests.** Offline unit tests use recorded fixtures; the default run never touches the network. Live tests are
  marked `@pytest.mark.live` and run on demand or on a schedule.
- **Accessibility.** Colour never carries meaning alone: any colour in CLI output, the scoreboard or docs is
  paired with a glyph or a word (`✓ works`, `▲ link`, `✗ blocked`).
- **Documentation is part of done.** Every change keeps these current: `CONTRIBUTING.md` (adding a brand),
  `docs/architecture.md`, `docs/adapters.md` (YAML schema and Python API), `docs/brands.md`, the integration
  pages, and docstrings and type hints on public code. Public docs are written for strangers: no internal
  hostnames, IP addresses, or details of anyone's home, devices or rooms.
- **Behaviour changes come with tests,** and a schema change comes with its row in `docs/adapters.md`.
- **Commits:** small, with clear messages. Stage only the files you changed (`git add -- <paths>`).

## Library notes

- The MCP SDK 2.x renamed `FastMCP` to `MCPServer` (`from mcp.server.mcpserver import MCPServer`).
- selectolax 1.0 dropped `selectolax.parser`; use `selectolax.lexbor` (`LexborHTMLParser`).
