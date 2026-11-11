# Contributing to Manual Beagle

Thank you for helping. The most useful thing you can add is a **brand adapter** for something you own: it teaches
Manual Beagle one maker's support site, it's usually a short YAML file, and it takes about 15 minutes.
[docs/brands.md](docs/brands.md) lists every brand, its status (✓ works, ▲ partial, ▲ link, ✗ blocked) and what
would help it.

Other good contributions: a bug report with the command you ran and its output, a fix, a test, a clearer sentence
in the docs, or a new place to file manuals (a sink: see [docs/architecture.md](docs/architecture.md)). For anything
larger than a brand or a fix, open an issue first so we can agree on the shape before you write it.

- [Set up](#set-up)
- [Add a brand in 15 minutes](#add-a-brand-in-15-minutes)
- [When YAML isn't enough: a Python adapter](#when-yaml-isnt-enough-a-python-adapter)
- [Tests](#tests), and [when the nightly run says an adapter broke](#when-the-nightly-run-says-an-adapter-broke)
- [Changes other than adapters](#changes-other-than-adapters)
- [The fetching rules](#the-fetching-rules)
- [The other rules](#the-other-rules)
- [What a good pull request looks like](#what-a-good-pull-request-looks-like)

## Set up

You need Python 3.12 or newer and git.

```
git clone https://github.com/manualbeagle/manualbeagle
cd manualbeagle
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

On Windows the last line is `.venv\Scripts\pip install -e ".[dev]"`, and the commands below are in
`.venv\Scripts\`. Activate the virtual environment (`source .venv/bin/activate`, or `.venv\Scripts\activate` on
Windows) and check that everything works:

```
pytest                                   # the offline tests: no network, a few seconds
manualbeagle adapters list               # every adapter, its status and its test cases
manualbeagle find LG WM5700HVA --no-search
```

Optional: `pip install pre-commit` and `pre-commit install` run ruff on every commit, as CI does.

## Add a brand in 15 minutes

We'll walk through a real adapter, [`defs/alorair.yaml`](src/manualbeagle/adapters/defs/alorair.yaml), which finds
AlorAir dehumidifier manuals. Every adapter lives in `src/manualbeagle/adapters/defs/<id>.yaml`; the id is the
file name (lower case, underscores: `ge_appliances`).

### 1. Find how the maker's site lists documents (5 minutes)

Before writing anything, check two things in a browser:

- **Is the site the maker's own, and reachable?** Manual mirrors (ManualsLib and the like) get no adapter. Read
  the maker's terms of use and note the URL and what they say about automated access in the adapter's header (a
  lookup counts as browsing; see [the fetching rules](#the-fetching-rules)). If the site shows a CAPTCHA, a
  "checking your browser" page, a login, or answers a script with HTTP 403, it's `status: blocked`: we never get
  around those (step 2 shows how to write one).
- **Where do the PDF links come from?** Open a product's support page with the developer tools' Network tab
  open, and look for the request that carries the PDF links. It's usually one of:
  - plain links in the page's HTML (the easiest; `alorair.yaml`, `dewalt.yaml`);
  - JSON embedded in the page, such as `<script id="__NEXT_DATA__">` (`ge_appliances.yaml`, `py/lg.py`);
  - a JSON API the page calls (`py/samsung.py`, `py/mitsubishi_electric.py`).

Note the URL pattern. For AlorAir, the product page is `https://www.alorair.com/product-details/alorair-galaxy-60`
for the Galaxy 60, and its downloads section has a label followed by a link:

```html
<span>User Guide</span>
<a href="https://www.alorair.com/pdf_files/product_guides/245/Galaxy-60-User-Manual-Outline.pdf"
```

### 2. Write the YAML (5 minutes)

Copy the closest existing adapter and edit it. Here is `alorair.yaml`, line by line:

```yaml
# AlorAir (crawlspace and basement dehumidifiers, air scrubbers).
# Site: www.alorair.com/product-details/alorair-<model as a slug> is the product page; its downloads list each
# document as a label ("User Guide", "Specification Sheet") followed by a link to /pdf_files/product_guides/.
# Known gaps: a model whose page slug isn't "alorair-" plus its name isn't found; the page is about 2 MB.
# Last verified: 2026-11-03.
```

Every adapter starts with this **header**: the brand, the site and how the adapter searches it, its known gaps,
and the date someone last checked it against the live site. It's the first thing the next contributor reads when
the site changes.

```yaml
brand: AlorAir                      # the name people type; matched ignoring case and punctuation
aliases: [AlorAir Solutions, Alorair]   # other names on rating plates, receipts and inventories
domains: [alorair.com]              # where the maker's pages and PDFs live: these rank first
status: works                       # works | partial | link | blocked | untested
categories: [dehumidifiers, air scrubbers]
manual_page: https://www.alorair.com/product-details/alorair-{model_slug}   # a page a person can open
notes: >-
  The product page's downloads (User Guide, Specification Sheet) under /pdf_files/product_guides/.
```

The brand and its aliases are also how a scanned rating plate gets its brand: the web UI's plate reader looks for
them, as whole words, in the plate's text. So add the names that are printed on the plates themselves (sub-brands
such as Maytag under Whirlpool, or the maker's full name).

`notes` is the one line shown in [docs/brands.md](docs/brands.md). `manual_page` is always offered as a link, so
even when the search finds nothing the user gets somewhere useful.

```yaml
search:
  - url: https://www.alorair.com/product-details/alorair-{model_slug}
    format: regex
    results: '<span>\s*(?P<label>[^<]{3,60}?)\s*</span>\s*<a href="(?P<url>https://www\.alorair\.com/pdf_files/product_guides/[^"]+?\.pdf)"'
    fields: {url: url, title: '=AlorAir {label}', kind: label}
```

A **search step** is one request and how to read results out of the response. `{model_slug}` is a template: for
"Galaxy 60" it becomes `galaxy-60`. `format: regex` means `results` is a regular expression with named groups,
and each match is one result. `fields` says where each part of a result comes from: `url: url` takes the `url`
group; `title: '=AlorAir {label}'` builds a title from the `label` group (a leading `=` means "a template, not a
group name"); `kind: label` passes the label through `kinds:` below. The step is tried with each variant of the
model (`GALAXY 60`, `GALAXY-60`, `GALAXY60`), most specific first, until one finds something.

Steps can also read JSON (`format: json`, with JSONPath) or HTML (`format: html`, with CSS selectors), POST forms
or JSON bodies, and filter results. A site that lists products by name often needs the model as a whole name,
so "G30" doesn't match "G30 Edge": `keep_title` with `{model_re}` does that (see `defs/petkit.yaml` and
`defs/eufy.yaml`). [docs/adapters.md](docs/adapters.md) is the complete reference.

```yaml
kinds:
  User Guide: owners_manual
  User Manual: owners_manual
  Specification: spec_sheet
  Installation: install_guide
```

`kinds` maps the site's own labels to Manual Beagle's document kinds (exact match first, then a label that
contains the key). A label it doesn't list is guessed from its words.

A brand whose site you can't search (terms that forbid it, a bot wall, a login) still gets a short adapter, with
`status: blocked` or `status: link`, no `search:`, its `domains` and a `manual_page`: see
[`defs/canon.yaml`](src/manualbeagle/adapters/defs/canon.yaml). Web search then ranks the maker's own PDFs first.

### 3. Add test cases (1 minute)

```yaml
tests:
  - model: Galaxy 60
    note: Sentinel-style crawlspace dehumidifier
    expect: {kind: owners_manual, url_contains: Galaxy-60-User-Manual}
  - model: Sentinel HD55
    note: crawlspace dehumidifier
    expect: {url_contains: pdf_files/product_guides}
```

Use real models, ideally ones you own, and at least two. `expect` says what a good result looks like; one of the
top eight results must meet all of it. A case that can't pass today gets `skip: <reason, with the date>`.

### 4. Record the fixtures and run the tests (3 minutes)

```
manualbeagle adapters test alorair --record -v
```

This runs each test case against the live site, politely, and saves the site's responses to
`tests/fixtures/alorair/galaxy-60.json` and `sentinel-hd55.json`. Each line starts with a glyph and a word
(`✓ pass`, `✗ fail`, `– skip`); `-v` lists the top results of a case that didn't pass, so you can see what the
adapter found instead. Then replay them offline, as CI does:

```
manualbeagle adapters test alorair
pytest
```

Look at the fixture files before you commit them. They hold only the parts of each response the adapter reads
(regex and html steps keep their matches; JSON is kept whole, so keep JSON requests narrow) and never a PDF's
body. A fixture over a few hundred kilobytes needs trimming: see `trim_fixture` below.

### 5. Finish (1 minute)

```
ruff check src tests
ruff format src tests
manualbeagle adapters scoreboard --write docs/brands.md
```

The last command regenerates the brand table from every adapter's YAML and an offline run of its tests. Commit
the YAML, the fixtures and `docs/brands.md` together, and open a pull request.

## When YAML isn't enough: a Python adapter

Some sites need two requests where the second depends on the first, or a match that a regex can't express. Then
the YAML names a Python class and still holds everything else (brand, domains, status, tests):

```yaml
python: levoit:Levoit        # class Levoit in src/manualbeagle/adapters/py/levoit.py
```

Here is the shape of [`py/levoit.py`](src/manualbeagle/adapters/py/levoit.py), shortened:

```python
"""Levoit: air purifiers, humidifiers, fans, vacuums.

Site: levoit.com/pages/user-manuals lists every product as a card, the product's name in its heading and a
download link to its manual. The page holds all cards at once, so one request finds any model. ...
Known gaps: products are named, not numbered (LAP-C201S-AUSR is "Core 200S") ...
Last verified: 2026-11-03.
"""

from collections.abc import Iterable

from ...models import Candidate, Query
from ..base import Adapter, Context

PAGE = "https://levoit.com/pages/user-manuals"


class Levoit(Adapter):
    """Levoit's user-manuals page; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Return the manual of every product card whose name contains the model."""
        want = model_pattern(q.model)
        r = ctx.http.get(PAGE)                      # always through ctx.http
        ctx.note(f"levoit: {r.url} -> {r.status}")
        if not r.ok:
            return []
        out = {}
        for name, href in CARD.findall(r.text):
            if want.search(name):
                out.setdefault(href, self.candidate(
                    url=href,
                    title=f"Levoit {name} user manual",
                    kind=self.kind_for("manual", name, href),   # through the YAML's kinds:
                    models=[q.model],
                    page_url=PAGE,
                ))
        return list(out.values())

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep only the cards, in the shape find() reads."""
        ...
```

The points that matter:

- **The module docstring is the header** (site, how it searches, known gaps, last verified).
- **Every request goes through `ctx.http`**, never `httpx` or `requests` directly. That client is what keeps
  requests polite, refuses private addresses, stops at bot walls, and records and replays fixtures.
- **`find()` returns candidates and decides nothing else.** Ranking, checking the PDF's text and downloading
  happen later in the pipeline. Use `self.candidate(...)` so the result is tagged with your adapter, and
  `self.variants(q)` to try the model's variants, most specific first.
- **`trim_fixture()`** cuts a recorded response down to what `find()` reads, so a 2 MB page becomes a few
  kilobytes of fixture. It's optional, but write it for any big page.
- **Docstrings and type hints** on the class and its public methods: ruff's docstring rules are on for `src/`.

Before writing a new class, check the **shared Python adapters**: one class serves every maker on the same
platform, configured from the YAML's `options`. [`py/shopify.py`](src/manualbeagle/adapters/py/shopify.py) covers
makers that sell from their own Shopify store and link manuals in product descriptions (see `defs/wen.yaml`);
[`py/zendesk.py`](src/manualbeagle/adapters/py/zendesk.py) covers makers whose support site is a Zendesk help
centre (see `defs/span.yaml`). A new brand on one of those platforms is a YAML file only:

```yaml
python: shopify:Shopify
options:
  store: https://wenproducts.com
```

The full Python API is in [docs/adapters.md](docs/adapters.md#a-python-adapter).

## Tests

| Command | What it runs |
|---|---|
| `pytest` | Every offline test: the core unit tests and every adapter's test cases replayed from `tests/fixtures/`. No network. This is what CI runs. |
| `pytest -m live` | Every adapter's test cases against the real sites, downloading each hit to check `text_contains`. Slow; run it for the adapters you changed: `pytest -m live -k alorair`. |
| `manualbeagle adapters test <id>` | One adapter's cases, offline. |
| `manualbeagle adapters test <id> --live -v` | One adapter's cases against the live site, without saving anything. |
| `manualbeagle adapters test <id> --record -v` | Live, saving the fixtures. |
| `manualbeagle adapters test <id> --live --check-text` | Live, downloading each hit to check its text. |
| `manualbeagle adapters test --live --json live.json` | Every adapter live, with the results as JSON for the two commands below. |
| `manualbeagle adapters report live.json` | What broke in that run, as Markdown. |
| `manualbeagle adapters scoreboard --live live.json` | The brand table with a Live column. |

The default test run never touches the network. A test that must (anything live) is marked `@pytest.mark.live`.

### When the nightly run says an adapter broke

A GitHub Actions workflow runs every adapter's cases against the live sites each night, which is how we notice
when a site changes. When a case breaks, the run opens (or updates) an issue labelled **live-adapters** listing
each broken case, its status and the top results the adapter found instead; the issue closes itself once
everything passes again. To pick one up:

1. Comment on the issue that you're taking that brand, so two people don't fix the same one.
2. Reproduce it: `manualbeagle adapters test <id> --live -v`. If it passes now, the site was only down for a
   night; say so on the issue and leave it.
3. If it still fails, open the maker's page in a browser and compare it with the adapter: a moved URL, a renamed
   label or a new JSON shape is the usual cause. A site that now shows a bot wall or a login is `✗ blocked`: set
   `status: blocked`, add `skip:` with the date to its cases, and don't try to get around it.
4. Fix the adapter, update its header's `Last verified:`, re-record with `--record -v`, regenerate
   `docs/brands.md`, and open a pull request that says `Fixes #<issue>` for that brand.

## Changes other than adapters

Fixes and features outside `adapters/` follow the same checks and rules. [docs/architecture.md](docs/architecture.md)
maps every module and the main types; read the part you're changing first. Some starting points:

| To change | Look at | Try it with |
|---|---|---|
| How results are found and ranked | `pipeline.py`, `verify.py`, `normalize.py` | `manualbeagle find <brand> <model> --why` |
| Downloads, page text, OCR, the index | `library.py` | `manualbeagle fetch …`, `manualbeagle search "<words>"` |
| The web UI and its HTTP API | `web/` | `manualbeagle serve --web --open` ([docs/web.md](docs/web.md)) |
| The MCP server | `mcp_server.py` | `manualbeagle serve --mcp` ([docs/integrations/mcp.md](docs/integrations/mcp.md)) |
| Homebox, Paperless-ngx, Donetick, the folder sink, Home Assistant | `sinks/`, `sources/`, `sync.py` | each command's `--dry-run`, and the pages in [docs/integrations/](docs/integrations/) |
| A web-search engine, a sink or an AI provider | [Plugins](docs/architecture.md#plugins) in docs/architecture.md | a test with recorded or mocked responses (`respx`) |

- **Tests come with behaviour changes.** Network calls in tests are recorded fixtures or `respx` mocks, never the
  real service; an inventory's API (Homebox, Paperless-ngx, Home Assistant) is mocked the way `tests/test_homebox.py`
  and its neighbours do it.
- **A new setting** goes in `config.py` with its default, the example in docs/architecture.md (*Configuration*),
  and the page that documents the feature. A secret is never a setting's value: it comes from an environment
  variable, a file or a command, through `read_secret()`.
- **A new dependency** should be optional if only one feature needs it (an extra in `pyproject.toml`, imported
  inside the function that uses it), and its licence must be compatible with Apache-2.0. Say which licence in the
  pull request.
- **The changelog:** add a line under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for anything a user would
  notice, in the section it belongs to (Added, Changed, Fixed, Removed).
- **Releases** are made by the maintainers; [docs/releasing.md](docs/releasing.md) has the steps.

## The fetching rules

Manual Beagle fetches a manual for the person who owns the product, on their own machine. Every adapter and every
change keeps to these rules; a pull request that breaks one won't be merged however useful it is.

- **One request at a time per site, with a delay between them** (1.5 seconds by default), and a User-Agent that
  names the project and links to its repository. `ctx.http` does all of this; that's why adapters must use it.
- **robots.txt doesn't apply to a lookup.** A lookup is one person asking for one product's manual, as they would
  in a browser, so adapters may use paths robots.txt disallows (the `respect_robots` setting turns the check back
  on). That makes the next rule strict:
- **Nothing that crawls or lists a site in bulk.** An adapter fetches what one lookup needs: a search, a product
  page, a document list. Never a loop over a catalogue, a sitemap, or every product.
- **Never get around a CAPTCHA, a bot wall or a login.** No browser automation to pass a challenge, no spoofed
  browser User-Agent or headers, no cookies copied from a browser, no proxies, no retry loops against a 403 or
  429. The client raises `Blocked` when it sees a challenge page; let it. Mark the brand `blocked` and let web
  search cover it.
- **A lookup is browsing, not scraping.** One lookup that one person starts, for one product's manual, is treated
  like that person opening the site in a browser. So an adapter may search a maker's own site even when its terms
  ban scraping, robots or automated collection in general: those clauses are about collecting a site in bulk,
  which the rule above already forbids. Note the terms URL, what they say and the date you read them in the
  adapter's header. If a maker asks the project to stop, its brand becomes `status: link` straight away.
- **No adapter for manual mirrors.** Sites that copy manuals, such as ManualsLib, get at most a link to their
  search page for a person to open (`link_only`), and `fetch` refuses their URLs.
- **Never host, share or mirror PDFs.** No PDFs in the repository (fixtures record `%PDF- (body not recorded)`
  instead of the body), and nothing that uploads manuals anywhere but the user's own inventory.

## The other rules

- **No AI required.** Every feature has a rules-only path, and AI plugins may only add to it. Presets, defaults,
  docs and tests use only models from US and EU labs: Claude, OpenAI, and Llama, Gemma or Mistral through Ollama.
- **Dependencies must be compatible with Apache-2.0.** So no PyMuPDF (AGPL): PDF text comes from pypdfium2, and
  OCR from OCRmyPDF with Tesseract, an optional external tool. Say why in the pull request when you add one.
- **Colour never carries meaning alone.** Any status in the CLI, the scoreboard or the docs pairs a colour with
  a glyph or a word (`✓ works`, `▲ link`, `✗ blocked`), so it reads for colour-blind users and in plain text.
- **Docs are part of the change.** Update `docs/architecture.md`, `docs/adapters.md`, this file and docstrings
  along with the code, and regenerate `docs/brands.md`. Public docs are written for strangers: no hostnames,
  addresses or details of your own home in examples.
- **Style:** ruff (`ruff check` and `ruff format`, line length 110), type hints on public functions, and
  Google-style docstrings on public modules, classes and functions.

## What a good pull request looks like

- **One brand or one change per pull request,** with a title that says what it does: "AlorAir adapter on the
  product page's downloads, 2 models pass live".
- **Small commits with clear messages.**
- **For a new or changed adapter:** the header with today's date as "Last verified", at least two real test
  cases, freshly recorded fixtures, the output of `manualbeagle adapters test <id> --live -v` in the description,
  and a regenerated `docs/brands.md`.
- **Green checks:** `ruff check src tests`, `ruff format --check src tests` and `pytest` all pass.
- **Docs updated** for anything a user or contributor would notice.
- **The fetching rules kept.** The pull request template has a checklist for all of this.

By contributing you agree that your work is licensed under the [Apache License 2.0](LICENSE). Everyone taking
part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md). To report a security problem, don't open an
issue: see [SECURITY.md](SECURITY.md).
