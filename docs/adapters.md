# Writing a brand adapter

An adapter teaches Manual Beagle one maker's support site. Most are a short YAML file; sites that YAML can't
describe get a small Python class as well. Every adapter carries its own test cases, which run offline in CI from
recorded responses and against the live site every night.

Adapters live in `src/manualbeagle/adapters/defs/<id>.yaml`. The id is the file name. For a guided first adapter,
read [CONTRIBUTING.md](../CONTRIBUTING.md#add-a-brand-in-15-minutes); this page is the reference.

- [Before you start](#before-you-start)
- [A YAML adapter](#a-yaml-adapter), with the [complete schema](#the-yaml-schema)
- [A Python adapter](#a-python-adapter) and [the Python API](#the-python-api)
- [Shared Python adapters](#shared-python-adapters)
- [Test cases and fixtures](#test-cases-and-fixtures)

## Before you start

- **Find how the maker's own site lists documents.** Open the product's support page in a browser with the
  developer tools' Network tab open. You're looking for one of:
  - PDF links right in the page's HTML (the easiest: see `alorair.yaml`, `dewalt.yaml`);
  - JSON embedded in the page (`<script id="__NEXT_DATA__">` and the like: see `ge_appliances.yaml`, `py/lg.py`);
  - a JSON API the page calls (see `py/samsung.py`, `py/toyota.py`, `py/mitsubishi_electric.py`).
- **Rules.** Never get around a CAPTCHA, a bot wall (Cloudflare, Akamai, PerimeterX challenge pages, 403s to
  scripts) or a login, and write no adapter for a manual mirror. In those cases write
  a `status: blocked` (or `link`) adapter with no search steps and a `manual_page` a person can open; Manual Beagle
  falls back to web search. robots.txt is for crawlers and isn't checked on lookups (a lookup is one person asking
  for one product's manual); the `respect_robots` setting turns it back on. Nothing may crawl or list a site in
  bulk: an adapter fetches only what one lookup needs. A maker's terms that ban scraping or automated collection
  in general don't rule out an adapter, since a lookup is one person's browsing, not collection; note the terms
  URL and what they say in the header, and if the maker asks the project to stop, make the brand `link`.
- **Be polite while you build.** The client sends one request at a time per site, with a delay between them and a
  User-Agent naming the project. Don't loop over a catalogue.

## A YAML adapter

```yaml
# Example Appliances (US).
# Site: support.example.com. The search API returns JSON with one entry per document.
# Known gaps: commercial models aren't listed.
# Last verified: 2026-11-03.
brand: Example Appliances           # the name people type; matched ignoring case and punctuation
aliases: [Example, Example Home]    # other names on rating plates and receipts
domains: [example.com, cdn.example-docs.com]   # where its pages and PDFs live: ranks them first
status: works                       # works | partial | link | blocked | untested
categories: [laundry, kitchen]
manual_page: https://support.example.com/search?q={model_q}   # a page a person can open
notes: >-
  One line for docs/brands.md.
model:
  strip_suffix: ['\.[A-Z]{5,8}$']   # regexes: EX1234.ABCUS -> EX1234 (tried after the full model)
search:
  - url: https://support.example.com/api/docs?model={model_q}
    format: json                    # json | html | regex
    results: $.documents[*]         # JSONPath (json), CSS selector (html), or a regex with named groups
    fields:
      url: $.pdfUrl                 # required
      title: $.name
      kind: $.category              # mapped through kinds:
      language: $.lang
kinds:
  "Owner's Manual": owners_manual
  "Installation Guide": install_guide
tests:
  - model: EX-4410W
    expect: {kind: owners_manual, text_contains: EX-4410}
  - model: EX-2200.ASTEUS
    expect: {kind: install_guide}
```

The comment block at the top is the adapter's **header**, and every adapter has one: the brand, the site and how
the adapter searches it, its known gaps, and the date it was last verified against the live site.

**Templates** in `url`, `params`, `json`, `form`, `headers`, `url_template`, `keep_url`, `keep_title` and
`manual_page`:

| Template | Value for the model `Galaxy 60 / X` |
|---|---|
| `{model}` | the variant, upper case: `GALAXY 60 / X` |
| `{model_lower}` | `galaxy 60 / x` |
| `{model_q}` | for a query string: `GALAXY+60+%2F+X` |
| `{model_path}` | for a path segment: `GALAXY%2060%20%2F%20X` |
| `{model_compact}` | letters and digits only: `GALAXY60X` |
| `{model_slug}` | lower case, hyphens: `galaxy-60-x` |
| `{model_re}` | a regex for `keep_title` or `keep_url`: the words escaped, joined by any spaces, underscores or hyphens: `GALAXY[\s_-]*60[\s_-]*/[\s_-]*X` |
| `{brand}` | the canonical brand (the adapter's `brand`) |
| `{year}`, `{year_or_blank}` | the model year, or empty (vehicles) |

Each search step is tried with every model variant, from the most specific, until one finds something. A string
whose braces aren't all templates is used as is, so a regex quantifier such as `\d{2}` is safe on its own; in a
string that also uses a template, write it `\d{{2}}`.

**Fields** by format:
- `json`: JSONPath relative to each result (`$.pdfUrl`); a value without `$` is a literal.
- `html`: `a.title@href` (an attribute of a child), `h3` (a child's text), `@href` (the result's own attribute),
  `.` (its own text), `=literal`.
- `regex`: a field names a group (`url: url`), or `=text with {group}` builds one from several groups. See
  `dewalt.yaml`.

**Filters** on a step: `keep_url`, `drop_url` and `keep_title` (regexes; `keep_url` and `keep_title` may use the
templates, such as `keep_url: '{model_compact}'`, and then a quantifier is written `{{2}}`), `base_url` for
relative links, and `url_template` to build the final URL from a field (`"https://x/files/{url}"`).

**Kinds** are `owners_manual`, `install_guide`, `quick_start`, `parts_list`, `spec_sheet`, `service_manual`,
`warranty`, `maintenance_guide`, `navigation_manual`, `energy_guide`, `other`. A label not in `kinds:` is guessed
from its words.

**Vehicles:** set `vehicle: true`; the model year is split off the model ("2022 Sienna") and available as
`{year}`. See `toyota.yaml`.

## The YAML schema

The YAML is validated by pydantic models in `src/manualbeagle/adapters/base.py`. An unknown key is ignored, so
check spelling against this list.

### AdapterSpec (the file)

| Key | Type, default | What it does |
|---|---|---|
| `id` | string, the file name | The adapter's id, used by `adapters test <id>`, `find --adapter <id>` and the fixtures folder. Leave it out: the file name is the id. |
| `brand` | string, **required** | The brand people type. Matched ignoring case and punctuation. It's also `{brand}` and the brand the library files documents under. |
| `aliases` | list of strings, `[]` | Other names for the brand: sub-brands, old names, how it's written on rating plates. Each finds this adapter, and the plate reader (`nameplate.py`) suggests the brand when it sees one on a scanned plate. |
| `domains` | list of strings, `[]` | The maker's domains, subdomains included. A result on one ranks as the maker's own (+40), the first is used for a `site:` web search, and `sync` attaches only links on them (or from the adapter itself). |
| `status` | `works`, `partial`, `link`, `blocked`, `untested`; `untested` | Shown in `docs/brands.md` with a glyph. `works`: it finds the documents itself. `partial`: some models. `link`: no searching adapter yet. `blocked`: the site refuses automated requests. |
| `notes` | string, `""` | One line for `docs/brands.md`. |
| `categories` | list of strings, `[]` | What the maker makes, for readers. Not used in searching. |
| `vehicle` | bool, `false` | The model may start with a year ("2022 Sienna"), which is split off into `{year}`. |
| `python` | string, none | `module:Class` under `manualbeagle.adapters.py` that searches instead of `search` (see below). |
| `model` | mapping, `{}` | Model rules. `strip_suffix`: a list of regexes; each match is removed from the end of the model to make another variant, tried after the full model. |
| `search` | list of [SearchStep](#searchstep), `[]` | The steps. Every step runs, and their results are combined. |
| `kinds` | mapping of label to kind, `{}` | The site's labels for documents mapped to kinds. A label matches exactly (ignoring case) first, then by containing the key. |
| `kind_default` | kind, none | The kind when neither `kinds` nor the label's words say. |
| `link_only` | bool, `false` | Mark every result link-only: never downloaded, offered for a person to open. |
| `manual_page` | template string, none | A page a person can open for the model, always added as a link-only result. |
| `options` | mapping, `{}` | Passed as is to a Python adapter (`self.spec.options`). |
| `family_manuals` | bool, `false` | The maker's manuals cover a range and don't name each model, so `sync` and `batch --fetch` upload this adapter's PDF even when its text doesn't name the model. Web-search results never get this trust. |
| `tests` | list of [TestCase](#testcase), `[]` | Real models and what each should find. |

```yaml
brand: LG
aliases: [LG Electronics, LGE]
domains: [lg.com, lge.com]
status: works
python: lg:LG
model:
  # WM5700HVA.ASSEVUS -> WM5700HVA (sales code -> customer model code)
  strip_suffix: ['\.[A-Z]{5,8}$']
manual_page: https://www.lg.com/us/support/product/lg-{model}
```

### SearchStep

| Key | Type, default | What it does |
|---|---|---|
| `url` | template string, **required** | The address to request. |
| `method` | `GET` or `POST`; `GET` | |
| `params` | mapping, none | Query-string parameters (templates allowed in values). |
| `json` | any, none | A JSON request body (templates allowed in strings). |
| `form` | mapping, none | A form-encoded request body. |
| `headers` | mapping, none | Extra request headers, such as `Accept: application/json`. Never a browser's User-Agent or cookies. |
| `format` | `json`, `html`, `regex`; `json` | How to read the response. |
| `results` | string, **required** | What one result is: JSONPath (`$.documents[*]`), a CSS selector (`li.doc`), or a regex with named groups (each match is a result). |
| `fields` | [FieldSpec](#fieldspec), **required** | Where each field of a result comes from. |
| `base_url` | string, the response's URL | What relative links are resolved against. |
| `url_template` | template string, none | Builds the final URL from the `url` field, available as `{url}`: `"https://x/files/{url}.pdf"`. |
| `keep_url` | regex (template), none | Keep only results whose URL matches (ignoring case). |
| `drop_url` | regex, none | Drop results whose URL matches. |
| `keep_title` | regex (template), none | Keep only results whose title matches. |
| `drop_title` | regex, none | Drop results whose title matches (other regions' or languages' copies, certificates). |
| `for_each_variant` | bool, `true` | `true`: stop at the first model variant that finds something. `false`: run every variant and keep all results. |

A step that answers with an error status finds nothing (and the next variant is tried); a bot wall stops the
whole adapter with `Blocked`. One URL listed under several labels is kept once, as an owner's manual if any label
says so.

```yaml
search:
  - url: https://support.example.com/api/search
    method: POST
    json: {query: "{model}", type: document}
    headers: {Accept: application/json}
    format: json
    results: $.hits[*]
    fields: {url: $.file, title: $.title, kind: $.docType}
    keep_url: '\.pdf$'
    for_each_variant: true
```

### FieldSpec

Only `url` is required. Each value is read with the step's format: JSONPath (`$.x`, or a literal without `$`) for
`json`; a selector spec (`a@href`, `h3`, `@href`, `.`, `=literal`) for `html`; a group name or `=template with
{group}` for `regex`.

| Key | What it becomes |
|---|---|
| `url` | The document's address (resolved against `base_url`, then through `url_template`). When a JSONPath finds several, the first. |
| `title` | Its title (whitespace collapsed). |
| `kind` | A label, mapped to a kind through `kinds`, then guessed from the label, title and URL. |
| `language` | Its language code, such as `en`. Non-English documents rank lower. |
| `models` | Models the site says it covers (one value or a list). |
| `page_url` | A page a person can open for it, when `url` isn't the document itself. |
| `published` | Its date, as the site writes it. |

```yaml
fields:
  url: a.download@href
  title: h3
  kind: =owners_manual
  page_url: '@data-product-url'
```

### TestCase

| Key | Type, default | What it does |
|---|---|---|
| `model` | string, **required** | A real model, as a person would type it. Names the fixture file. |
| `year` | int, none | The model year, for vehicles. |
| `note` | string, `""` | What the product is, for readers. |
| `expect` | [Expect](#expect), `{}` | What a good result looks like. |
| `skip` | string, none | Why the case can't pass today, with a date. A skipped case isn't run. |

```yaml
tests:
  - model: Sienna
    year: 2022
    note: minivan
    expect: {kind: owners_manual}
  - model: RLC-810A
    skip: no support page for this model (2026-11-03)
```

### Expect

A case passes when the adapter returns at least `min_results` results and one of the top eight, after ranking,
meets every key that is set.

| Key | Type, default | The result must |
|---|---|---|
| `kind` | kind, none | be this kind of document. |
| `url_contains` | string, none | have this in its URL (ignoring case). |
| `url_regex` | regex, none | have a URL matching this (ignoring case). |
| `title_contains` | string, none | have this in its title (ignoring case, spaces and punctuation). |
| `text_contains` | string, none | be a PDF whose text contains this. Checked only in live runs with `--check-text` (and `pytest -m live`). |
| `min_results` | int, `1` | (of the adapter) return at least this many results. |
| `link_only` | bool, none | be link-only (`true`) or downloadable (`false`). |

```yaml
expect: {kind: owners_manual, url_contains: images.salsify.com, text_contains: FCM7SKWW, min_results: 2}
```

## A Python adapter

Add `python: <module>:<Class>` to the YAML and put the class in `src/manualbeagle/adapters/py/<module>.py`. The
YAML still holds the brand, domains, status and tests.

```python
"""Example Appliances (US).

Site: ... How it searches: ... Known gaps: ...
Last verified: 2026-11-03.
"""

from collections.abc import Iterable

from ...models import Candidate, DocKind, Query
from ..base import Adapter, Context


class Example(Adapter):
    """Example Appliances' document API; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Return the documents the API lists for the first model variant that has any."""
        for variant in self.variants(q):          # q.variants, most specific first
            r = ctx.http.get("https://support.example.com/api", params={"model": variant})
            if not r.ok:
                continue
            out = [
                self.candidate(url=d["pdf"], title=d["name"], kind=self.kind_for(d.get("type"), d["name"]))
                for d in r.json()["documents"]
            ]
            if out:
                return out
        return []

    def trim_fixture(self, url: str, body: str) -> str:
        """Optional: cut a recorded response down to what find() reads, so fixtures stay small."""
        return body
```

Always fetch through `ctx.http` (never `httpx` or `requests` directly): it's what makes requests polite, refuses
private addresses, stops at bot walls, and records and replays fixtures. The module docstring is the adapter's
header, and ruff's docstring rules require a docstring on the class and on `find` and `trim_fixture`.

### The Python API

**`Adapter`** (`adapters/base.py`), the base class:

| Member | What it is |
|---|---|
| `spec` | The adapter's `AdapterSpec`; `self.spec.options` holds the YAML's `options`. |
| `id`, `brand` | The adapter's id and brand. |
| `names()` | The brand and its aliases. |
| `strip_rules()` | The `model.strip_suffix` regexes. |
| `variants(q)` | The query's model variants, most specific first (`[q.model]` when there are none). |
| `find(q, ctx)` | **Override this.** Return an iterable of `Candidate`s, empty when nothing is found. Raise nothing for "not found"; let `Blocked` and `FetchError` from the client propagate. |
| `trim_fixture(url, body)` | Override to keep only what `find` reads of a recorded response. Default: all of it. |
| `kind_for(label, title="", url="")` | A `DocKind` from the YAML's `kinds`, else guessed from the words, else `kind_default`. |
| `candidate(**fields)` | A `Candidate` with `source="adapter:<id>"` and `link_only` from the spec, unless given. |

**`Context`**: `ctx.http`, the `PoliteClient`; `ctx.note(text)` adds a line to the lookup's log.

**`PoliteClient`** (`http.py`): `get(url, params=..., headers=...)`, `post(url, json_body=..., data=...,
headers=...)` and `request(method, url, ...)` return a `Response`. They raise `Blocked` at a bot wall, a CAPTCHA
or HTTP 429 (pass `check_challenge=False` only for an API whose error pages aren't challenges), `UnsafeURL` for a
private address, and `FetchError` for a network error or a body over 25 MB. In a replayed test, a request that
wasn't recorded raises `CassetteMiss`.

**`Response`**: `status`, `url` (after redirects), `headers` (lower-case keys), `content` (bytes), `text`,
`json()`, `ok` (2xx) and `content_type`.

**`Query`** (`models.py`): `brand`, `model`, `year`, `kind`, `language` (default `en`), `region` (a country code, default `us`), `canonical_brand`, `variants`. Results whose URL names another language or country rank lower.

**`Candidate`** (`models.py`): `url` (required), `title`, `kind`, `language`, `source`, `page_url`, `link_only`,
`models`, `published`, `size_bytes`, `snippet`. Tracking parameters (`utm_*`, `gclid`, `msockid`…) are dropped from `url` and `page_url`. Leave `score` and `reasons` alone: ranking sets them.

**Helpers** in `adapters/yaml_adapter.py`: `template_vars(q, variant)` (the template table above as a dict) and
`fill(obj, vars)` (fill templates in a string, list or dict).

An adapter's `find` returns candidates and nothing else: it doesn't download PDFs, rank, or decide what to keep.
Return everything plausible for the model; ranking puts the best first.

### Shared Python adapters

Some sites are built on the same platform, and one Python class serves them all; the YAML names it and passes
`options`:

- `shopify:Shopify`, for a maker that sells from its own Shopify store and links manuals in the product
  description (see `defs/wen.yaml`). Options: `store` (the store's base URL), `pdf_hosts` (default
  `cdn.shopify.com`), `skip_title` (a regex for product titles to leave out, such as replacement parts).
- `zendesk:Zendesk`, for a maker whose support site is a Zendesk help centre that links manuals (PDFs, article
  attachments, Google Drive or Dropbox files) from its articles (see `defs/span.yaml`). Options: `base` (the help
  centre's root URL), `query` (default `"{model} manual"`), `also` (a regex for link texts to keep whatever model
  they name, for a manual that covers a whole range), `locale` (default `en-us`).

## Test cases and fixtures

Each test case is a real model and what it should find. `expect` can hold `kind`, `url_contains`, `url_regex`,
`title_contains`, `min_results` and `link_only`; a result in the top eight must meet all of them.
`text_contains` is checked only in live runs with `--check-text`, against the downloaded PDF. A case that can't
pass today gets `skip: <reason, with the date>`.

```
manualbeagle adapters test <id> --record -v   # run against the live site and save tests/fixtures/<id>/*.json
manualbeagle adapters test <id>               # replay offline, as CI does
manualbeagle adapters test <id> --live --check-text   # live, and download the hit to check its text
```

A case's fixture is `tests/fixtures/<id>/<model as a slug>.json` (with `-<year>` for vehicles). Fixtures hold the
site's responses (never PDFs). Keep them small: YAML regex and html steps keep only what they match, and a Python
adapter can define `trim_fixture`.

Finally run `ruff check src tests`, `ruff format src tests` and `pytest`, then regenerate the scoreboard with
`manualbeagle adapters scoreboard --write docs/brands.md`.

### The nightly live run

[`.github/workflows/live-adapters.yml`](../.github/workflows/live-adapters.yml) runs every case against the live
sites each night, with the same commands you can run yourself:

```
manualbeagle adapters test --live --json live.json            # every case live; results as JSON as well
manualbeagle adapters scoreboard --live live.json --write live-brands.md   # brands.md with a Live column
manualbeagle adapters report live.json --write report.md       # what broke, as Markdown; exit 1 if anything did
```

`--json` writes one object per case: `adapter`, `model`, `year`, `status` (`pass`, `fail`, `blocked`, `error`,
`skip`, `no-fixture`), `message`, and `top`, the three best results (`score`, `kind`, `title`, `url`). A case is
**broken** when its status is `fail`, `error` or `blocked`. The run's summary page shows the report and the live
scoreboard, and a scheduled run keeps one GitHub issue labelled `live-adapters` current: opened when something
breaks, updated each night it stays broken, and closed when every case passes again.
