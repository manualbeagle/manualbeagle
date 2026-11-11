# The web UI

`manualbeagle serve --web` runs a small web page and the JSON API behind it, on your own computer. It does what
the command line does: look up a brand and model and download the result, search every page of the library and
read it, run a list, and sync Homebox. On a phone it can also photograph an appliance's rating plate and read the
brand and model from it. It's a different front end on the same code: the same adapters, ranking,
library and rules, and the same polite client, so one request at a time per site, with a pause between them.

## Start it

The web UI needs the optional `web` extra (Starlette and uvicorn, both BSD-licensed):

```
pip install "manualbeagle[web]" # from a clone: pip install -e ".[web]" (the dev extra includes it)
manualbeagle serve --web --open  # http://127.0.0.1:8765/, opened in your browser
```

| Option | Default | What it does |
|---|---|---|
| `--host` | `127.0.0.1` | The address to listen on. Anything other than a loopback address needs a token (below). |
| `--port` | `8765` | The port. |
| `--token` | off | Require the secret link even on 127.0.0.1, for a computer other people log in to. |
| `--open` | off | Open the page in your default browser. |
| `--public-url` | none | The `https://` address of a reverse proxy in front of it ([Behind a reverse proxy](#behind-a-reverse-proxy)). Needs a token. |

Ctrl+C stops it. Batch and sync jobs live in memory and stop with it; everything they downloaded stays in the
library.

## The page

- **Scan a rating plate** (on Find): take a photo of the plate, or **Choose a photo** you already took, check the
  brand and model it read, and look them up. See [Scan a rating plate](#scan-a-rating-plate).
- **Find:** a brand, a model, and optionally a year (for vehicles), the kind of document you want, and when to
  use web search. You get the ranked results with their scores, each source's status (`✓ ok`, `✗ blocked`,
  `✗ error`), and "why this score". **Fetch** downloads a result into the library, reads every page (with OCR
  where a page has no text, when it's installed) and checks that its text names the model: `✓ saved`, or
  `▲ saved, but its text doesn't name the model`. A result marked `▲ link only` is a page for you to open, and
  one already downloaded says `✓ in library`.
- **Library:** search every page of every manual (the same full-text search as `manualbeagle search`, with
  word endings, `"phrases"`, `OR` and `prefix*`), then open a hit at its page. Below are the rating plate photos
  you've scanned, each with **Find →** and **Delete**, then every document, with a filter. With
  [Paperless-ngx](integrations/paperless.md) set up, the top of the page says how many manuals are in it, and
  **Send N to Paperless…** files the rest after a confirm; the reader has **Send to Paperless…** for one. **Read** shows a page's text, with your search words highlighted and the arrow keys to turn pages;
  **PDF** opens the file in your browser at that page; **Source** is where it came from.
- **Ask:** a question in your own words, such as "how do I clean the lint filter?", answered from the library's
  own manuals. See [Ask your manuals](#ask-your-manuals).
- **Batch:** paste a list or choose a file: CSV with `brand` and `model` columns (and `name`, if you like), or a
  YAML list. The file is read in the browser and only its text is sent to the server. Without **Download into
  the library** each row is looked up and graded (`✓ found`, `▲ link`, `▲ weak`, `✗ none`, `! search failed`,
  `– skip`), as `manualbeagle batch FILE` does. With it, each row gets sync's decision and the PDF is saved
  (`✓ manual`, `▲ link`, `✗ none`, `– skip`), as `batch --fetch` does, and the summary is coverage by brand. To
  copy the results to a folder, use `manualbeagle export DIR` or `batch --fetch --export DIR`.
- **Homebox:** whether Homebox is set up and answering, and **Sync**. A sync always starts as a dry run: it plans
  every item, downloads and checks the PDFs into the library, and writes nothing to Homebox. When it's done,
  **Attach N to Homebox…** asks you to confirm, then uploads the PDFs and attaches the links, showing
  `✓ attached` or `✗ refused` for each. The options are `sync homebox`'s: only items whose name contains some
  text, items that already have a manual, links only, and links found by web search. With
  `[homebox] attach_links = false` only PDFs are attached.
  **Import from Home Assistant** reads [Home Assistant](integrations/homeassistant.md)'s devices (it changes
  nothing there) and shows each one as `+ new`, `✓ in Homebox` or `– skip` with the reason. **Add…** adds one
  device as a Homebox item in its area, **Add N to Homebox…** the ticked ones; both ask first. **Look up their
  manuals** runs them through Batch.
- **Maintenance:** tasks with an interval read from the manuals in the library, such as "clean the filter every
  2 weeks" or "replace the hoses every five years", for you to approve one at a time. See
  [Maintenance](#maintenance).
- **Settings:** what the server uses, in seven groups (lookups, web search, Homebox, Home Assistant,
  Paperless-ngx, maintenance with Donetick, the AI model), each
  with where its value comes from: `✎ changed here`, `● config file`, `○ default`, or `▲ set by the server's
  environment` (locked here). **Save** keeps the changes in `settings.json` beside the library and uses them at
  once (not while a batch or sync runs); they win over config.toml, and **Use the file's value** takes one away.
  **List Ollama's models** asks the Ollama address for its models, to pick one. Anyone with the page's link can
  change these, including the addresses the server may reach on your network. API keys, where they're read
  from, the library's folder and extra adapter folders can't be changed here: they stay in config.toml or the
  server's environment, so the page can never run a program or read a secret. Homebox's, Home Assistant's and
  Paperless's keys go only to the address set beside them there: an address changed on this page alone gets no
  key, and the page says `✗ token not sent` (or `✗ API key not sent`) and why.

One job (a batch, a sync, an attach, a Paperless send, or reading the manuals for maintenance) runs at a time; **Cancel** stops it after the row it's on. A reload of
the page picks up a job that is still running.

Every status on the page is a glyph and a word. Colour only adds to them (blue for ✓, orange for ▲, magenta for
✗), and the page follows your system's light or dark setting.

## Ask your manuals

**Ask** answers a question from the manuals in your library, and only from them. Type it in your own words; to
keep to one maker or one manual, pick a **Brand** or a **Manual**. A brand or model number the question itself
names (and the library has) narrows it the same way.

It searches every page with the same full-text index as Library's search, using the question's words less the
little ones ("how", "do", "the"), and keeps the best four pages, at most three from one manual. Then:

- **With an AI model set up** (the `[ai]` table, the same one that reads rating plates; see
  [A vision model, if you want one](#a-vision-model-if-you-want-one)), the model reads those pages and writes a
  short answer that cites each page it used as `[1]`, `[2]`. Each number is a link that opens the Reader at that
  page with the question's words highlighted, and the pages below are marked `✓ cited` or `○ read, not cited`.
  When the pages don't answer the question the model is told to say so, and the page shows
  `– the manuals don't say` rather than a guess.
- **Without one**, you get the best pages, each with its snippet and a link to read it: `▲ the best pages (no AI
  model wrote an answer)`.

The status above the answer is always a glyph and words: `✓ answered from 2 of 4 pages`, `▲ answered, but the
model cited none of the pages it read`, `– the manuals don't say`, `✗ the model couldn't answer (…)` (the best
pages are still shown), or `– no page of the library matches`. The line under the heading says which model
writes the answers and whether the text leaves your network: with Claude, your question and the text of the
pages it reads are sent to Anthropic (see [privacy.md](privacy.md)).

A small model such as `gemma3` or `gemma4:e2b` on Ollama is enough: it gets only the question and about 1,500
characters from each of four pages, and answers in a few seconds on a GPU (a minute or more on a CPU). The same
answer is `manualbeagle ask "..."` on the command line and the `ask_manuals` tool over MCP.

## Maintenance

Manuals say how often to look after things: "clean the filter every two weeks", "test the safety reversal system
every month", "replace the hoses every five years". The Maintenance page finds those and, when you approve one,
adds it to the item in Homebox, whose notifier then reminds you on the day, or, with `[maintenance] target =
"donetick"`, makes it a [Donetick](integrations/donetick.md) chore that repeats on the task's interval.

1. **Read the manuals.** Every document in the library is read for a maintenance verb (clean, replace, check,
   test, descale, oil...) with an interval of a week to ten years: in a sentence, in a bullet under a part's name
   ("Air filter / • Clean every 2 weeks"), under a heading such as "EVERY MONTH", or in a frequency table
   ("Filter  Once every week  Every 3 months" under "Cleaning Frequency  Replacement Frequency"). Daily tasks,
   tasks after each use and hours of use aren't suggested. The rules alone do this; no AI is needed.
2. **Also ask the model** (when one is set up in Settings) asks it about each page with an interval on it. It's
   shown the rules' findings and may word them more clearly or add tasks the rules missed (a table the PDF's text
   scrambled, say), but its answer is kept only when its quote is word for word on the page and its interval is
   one the page states. It never removes what the rules found. It's slower: about a quarter of a minute per page
   on a small local model.
3. **Decide.** Each suggestion shows the task, how often, the item it belongs to, and the manual's own words with
   a link to the page. Change the task's wording or the first date (default: one interval from today) if you
   like, then **✓ Add to Homebox** (**✓ Add to Donetick**, with the appliance's name to put in front of the task,
   when Donetick is the target) or **✗ Dismiss**. A dismissed suggestion is never suggested again (**Undo** brings
   it back). Nothing reaches Homebox or Donetick without that click.

Each document is matched to Homebox's items by brand (aliases count: Honeywell Home is Resideo) and model number
(ignoring case, spaces and dashes). A suggestion for a model no item has is kept under its brand and model; you
choose the item when you add it, or a later run matches it once the item has a model number. Reading the manuals
again only adds what's new: suggestions and your decisions are kept in the library's database (the `maintenance`
table), one per item, task and interval.

Homebox doesn't repeat maintenance entries. The entry's description says the interval, the manual and page, and
the quote, so when you mark it done you know when the next one is due. A Donetick chore repeats by itself; its
description says the same, with a link to the page when the UI runs with `--public-url`. **Approved maintenance
goes to** on the Settings page switches between them; suggestions filed before keep saying where they went.

`manualbeagle maintenance suggest [--ai]` reads the manuals from the command line and `manualbeagle maintenance
list [--status new|filed|dismissed|all] [--json]` lists the suggestions; approving is done on this page.

## Scan a rating plate

The rating plate (or nameplate) is the sticker or metal plate, usually inside a door, under a lid or on the back,
that gives the maker, model number and serial number. **Scan a rating plate** on the Find page opens the phone's
camera (on a computer, a file chooser), and **Choose a photo** beside it opens the phone's photo library, for a
plate photographed earlier. Either way:

1. The browser turns the photo upright and shrinks it to at most 2400 pixels on its long side, as a JPEG. Drawing
   it this way leaves the camera's metadata (location, device, time) behind, and the server strips any that's
   left before keeping it.
2. The server greys the photo and stretches its contrast (with Pillow, in the `web` extra), which matters for
   coloured plates such as a yellow power-tool label, then reads it with
   [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html) OCR and looks
   for the labels plates use: `MODEL`, `MODEL NO.`, `M/N`, `MOD.`, `MOD/MOD`, `MDL`, `CAT. NO.` for the model,
   and `SERIAL`, `SER`, `S/N` for the serial, on the same line or the next. The brand is any adapter's brand or
   alias written on the plate as a whole word, or one of a list of common makers (two-letter names such as LG
   and GE must be in capitals). A model-like word with no label in front of it is offered as a guess, never a phone number
   (`1-800-4-DEWALT`), a speed (`0-20000/min`) or a postcode.
3. The page shows what it read, each suggestion with how it was found: `✓ read` (after a label), `▲ guess`
   (no label), `◆ vision model`. Correct anything, then **Use these and find**: the brand and model are saved
   with the photo and Find runs. Nothing is looked up before you confirm.

Plates are read best close up, square on, filling the frame, without glare; the serial is shown but never sent
anywhere. OCR often confuses `O` and `0`, or `I` and `1`, so when both readings turn up, both are offered.

Tesseract is the only thing reading photos needs (OCRmyPDF is for PDFs). Without it, the photo is still kept and
the page asks you to type the brand and model. `manualbeagle nameplate PHOTO` reads a photo from the command line.

**Photos are kept** in the library's `photos/` folder, named by their content hash, with the brand, model and
serial you confirmed and the OCR text. The Library page lists them; **Delete** removes one.

### Add it to Homebox

Once you've confirmed the brand and model, and Homebox is set up (see
[integrations/homebox.md](integrations/homebox.md)), the scan offers **Add to Homebox…**. It writes nothing until
you've checked it all:

1. **The form.** Manual Beagle asks Homebox (reading only) for its locations and for any item that already has
   this maker and model number. Brands are matched by their usual name (`LG Electronics` is `LG`) and model
   numbers without case, spaces or dashes. If there is one, it's offered first, with whether it has a manual,
   so you add to it instead of making a duplicate; **Make a new item anyway** is the other choice. For a new
   item you can change the suggested **name** (the brand, the kind of product and the model, such as
   `LG Dishwasher LDF5545ST`), the manufacturer, model and serial number, and pick a **location**. **Attach the
   rating-plate photo** is ticked.
2. **The review.** **Review…** lists every write: the item (or the existing one), the photo, and the manual.
   **Back** changes something; **Write to Homebox** writes it.
3. **The writes**, each a row with `✓ done`, `– skipped` or `✗ failed`: the item is created with its
   manufacturer, model number and location (the serial is set straight after, since Homebox takes it only on
   an update); the photo is attached as the item's picture (as an extra photo on an existing item); then the
   manual is found and attached by the same rule as `sync homebox` (a PDF whose text names the model is
   uploaded, otherwise the maker's link if `[homebox] attach_links` is on). **Open in Homebox ↗** opens the item.

The kind of product in the suggested name is found by rules alone: words such as "dishwasher", "water heater"
or "pressure washer" on the plate or in the titles Find returned. When none turns up, the name is the brand and
model. An existing item's own fields are never changed, and it gets no manual if it has one already.

### A vision model, if you want one

A vision model can read worn, angled or oddly laid-out plates that OCR misses. It's optional and only adds a
suggestion: OCR always runs first. The page asks the model by itself when OCR missed the brand or model and the
model runs on your own network (Ollama); otherwise only when you press **Ask the vision model**, so a photo goes
to Anthropic only on a click. Its answer is trimmed to the model number. Set it up on the **Settings** page, or
in the `[ai]` table of the config:

```toml
[ai]
provider = "ollama"              # a model you run yourself; the photo stays on your network
model = "gemma3"                 # or "llama3.2-vision" (the default is gemma3)
url = "http://localhost:11434"   # where Ollama listens
```

```toml
[ai]
provider = "anthropic"           # Claude, through Anthropic's API; the photo is sent to Anthropic
model = "claude-opus-5-5"        # the default
api_key_env = "ANTHROPIC_API_KEY"   # the environment variable that holds your API key
```

With Anthropic the page notes that the photo leaves your network. Requests go through the same polite client as
everything else; a local model on a CPU can take a minute.

## On a phone (HTTPS)

The Scan button works over plain HTTP too, since choosing a photo needs no secure context. But a phone can only
install the page as an app over HTTPS, and plain HTTP lets anyone on the network read your library as it goes by.
So for a phone, run it [behind a TLS reverse proxy](#behind-a-reverse-proxy) with `--public-url`.

Then open `https://<name>/?token=<token>` on the phone once. It stores the token in a cookie that is HttpOnly,
SameSite=Strict, Secure over HTTPS, and lasts a year. To install it, use the browser's **Install app** or **Add to
Home Screen**. An installed app that doesn't share the browser's cookies (on iOS it doesn't) opens on a sign-in
page instead; paste the token there once.

Keep the proxy's name inside your own network or VPN unless you've thought hard about it: anyone with the token
can read your library and start syncs.

## Who can reach it

By default the server listens on 127.0.0.1, so only programs on this computer can reach it, and it answers only
requests whose Host header is a loopback name (`127.0.0.1`, `localhost`, `[::1]`). That stops a web site from
reaching it through a DNS trick. A request that changes something (a lookup, a download, a job) must be JSON,
and if it has an `Origin` it must be the page's own, so another site open in your browser can't send one. The
library's PDFs are served only from the library folder, and only for documents in its database.

Other accounts on the same computer can reach 127.0.0.1 too. If the computer is shared, add `--token`.

With `--host` set to any other address (to use it from another device), a token is always required. The server
takes it from the `MANUALBEAGLE_WEB_TOKEN` environment variable, or makes a random one, and prints a link that
carries it: `http://<host>:8765/?token=...`. Opening the link stores the token in a cookie for that site
(HttpOnly, SameSite=Strict); a script can send `Authorization: Bearer <token>` instead. The connection is plain
HTTP, so anyone who can watch your network can read what you look up and the pages you open. Anyone with the link
can read your library and run lookups and syncs. Put it behind a TLS reverse proxy (below), or keep it on
127.0.0.1.

Without the token, `/healthz`, the page's own files (`/static/`), the web app manifest and the service worker
still load, since browsers fetch a manifest without cookies; none of them holds any of your data. `/` without the token is a
sign-in page with a box for the token.

### Behind a reverse proxy

To reach it from a phone or another computer over HTTPS, keep it on 127.0.0.1 and put a TLS reverse proxy in
front of it on the same machine:

```
MANUALBEAGLE_WEB_TOKEN=<a long random string> manualbeagle serve --web --public-url https://manuals.example.org
```

`--public-url` adds the proxy's host name to the names the server answers and its origin to the origins allowed
to send changes, always requires the token, and marks the token cookie `Secure` when the URL is `https://`. The
proxy must pass the original `Host` header through (`proxy_set_header Host $host;` in nginx; Caddy does it by
default). When the token comes from `MANUALBEAGLE_WEB_TOKEN`, as it would in a service, the printed link leaves
it out, so a service's log never holds it: open `https://<name>/?token=<token>` once on each browser.

## Home Assistant and Paperless-ngx

Both are optional and documented on their own pages: [Home Assistant](integrations/homeassistant.md) (read only:
its devices offered as Homebox items) and [Paperless-ngx](integrations/paperless.md) (the library's manuals filed
there). With `[paperless] auto` on, the server files each new manual within five minutes of its download, and
soon after the **Fetch** button, while no job runs. Their endpoints are in the table below.

## The HTTP API

The page uses these, and so can a script. Every answer is JSON, and an error is `{"error": "..."}` with a 4xx
status. POST bodies are JSON objects (`Content-Type: application/json`), except a nameplate photo's.

| Method and path | What it does |
|---|---|
| `GET /healthz` | `ok` (plain text) while the server runs. The one path that needs no token, for monitoring. |
| `GET /api/status` | Version, the number of documents and pages, whether OCR (`ocr` for PDFs, `plate_ocr` for photos), web search, Homebox, Home Assistant and Paperless-ngx are set up, the vision model (`vision`, and `vision_leaves_network`), the document kinds. |
| `POST /api/find` | `{brand, model, year?, kind?, search: "auto"\|"always"\|"never"}` → the `FindResult` (query, ranked `candidates`, `adapters`, `searched`, `search_error`) plus `in_library`, which maps each URL already downloaded to its document. Saves nothing. |
| `POST /api/fetch` | `{candidate, brand, model}` → `{sha256, title, pages, ocr, check_score, check_reasons}`. Downloads one candidate (as `find` returned it) into the library. Refuses link-only candidates and manual mirrors, on every redirect hop; a bot wall stops the download as it does on the command line. |
| `GET /api/documents` | Every document: `sha256`, `brand`, `model`, `kind`, `title`, `pages`, `source_url`. |
| `GET /api/documents/{sha256}` | One document, with `ocr` and `fetched_at`. |
| `GET /api/documents/{sha256}/pages?start=1&count=1` | The text of up to 20 pages: `{page_count, pages: {"1": "..."}}`. |
| `GET /api/documents/{sha256}/pdf` | The PDF, inline. |
| `GET /api/search?q=&brand=&model=&limit=` | Full-text search, best first: `{rows: [{sha256, page, brand, model, kind, title, snip}], plain}`. In `snip`, each match sits between the control characters U+0002 and U+0003. Text that isn't valid FTS5 syntax is searched again as plain words, and `plain` is true. |
| `POST /api/ask` | `{question, brand?, model?, document?, pages?, use_model?}` → `{question, status, headline, answer, sources, terms, scope, model, leaves_network, error}`. `status` is `answered`, `not_found`, `pages_only`, `model_error` or `no_pages`; `headline` says it as a glyph and words. `answer` cites pages as `[n]`; each of `sources` is `{n, sha256, page, brand, model, title, kind, snip, cited, label}`, with matches in `snip` between U+0002 and U+0003. `document` is a sha256; `pages` (1 to 8, default 4) is how many pages the model reads; `use_model: false` skips the model. `terms` are the words searched for, to highlight; `scope` is the brand, model or document searched. 409 when `[ai]` names a provider that can't answer. Read-only. |
| `GET /api/homebox` | `{configured, ok, url, version}`, or an `error`. |
| `POST /api/homebox/new-item/preview` | `{brand, model, serial?, photo?, titles?}` → `{name, kind, brand, model, serial, locations: [{id, name, label}], matches: [{id, name, manufacturer, model, serial, location, manuals, photos, url}], attach_links}`. Reads Homebox and writes nothing. `photo` is a kept photo's sha256, whose OCR text and `titles` (from Find) give the kind of product. 502 when Homebox isn't set up or refuses. |
| `POST /api/homebox/new-item` | `{confirm: true, name, brand, model, serial?, location_id?, existing_id?, photo?}` → an `add` job (202) whose rows are the steps `{step: item\|serial\|photo\|manual, state: ok\|skip\|bad, text, url?, document?}`, and which has `item_url`. Without `confirm: true` it's refused. `existing_id` adds to that item instead of creating one (409 unless it has the same maker and model). |
| `GET /api/homeassistant` | `{configured, ok, url, version}`, or an `error`. Signs in and out of Home Assistant. |
| `GET /api/homeassistant/devices` | `{offers, summary, homebox, homebox_error}`. Each offer is `{device_id, name, manufacturer, ha_model, area, integration, action: new\|have\|skip, reason, brand, model, location, homebox_item, same_model}`. Reads Home Assistant and Homebox; writes nothing. 502 when Home Assistant isn't set up or refuses. |
| `POST /api/homeassistant/import` | `{device_ids: [...]}` (at most 500) → `{rows, summary}`: each row is the offer with `done`, `item_id` and `error`. Reads both again and adds only devices that are still `new`; the page asks first. |
| `GET /api/paperless` | `{configured, ok, url, version, auto, counts: {filed, exists, queued, failed}, unsent}`, or an `error`. Looks up queued sends once. |
| `POST /api/paperless/send` | `{sha256}` or `{all: true}` → a `paperless` job (202) whose rows are `{sha256, title, state: filed\|exists\|queued\|failed, message, document_id, task_id}`. One manual waits up to 90 s for Paperless to file it. 400 when nothing is left to send. |
| `POST /api/nameplate` | The body is the photo itself, `Content-Type: image/jpeg` or `image/png` (checked against its first bytes; at most 15 MB; send it upright). Strips its metadata, reads it, keeps it → `{photo: {sha256, media_type, brand, model, serial, taken_at}, reading: {brands, models, serials, text}, ocr, vision}`. Each suggestion is `{value, why, source, sure}`, best first; `source` is `ocr` or the vision model's name, `sure` is false for a guess. `ocr` is false when Tesseract isn't installed; `vision` names the optional vision model, or is null. Looks nothing up. |
| `GET /api/photos` | Every kept photo, newest first: `sha256`, `media_type`, `brand`, `model`, `serial`, `taken_at`. |
| `GET /api/photos/{sha256}` | One photo, with its `ocr_text`. `POST` `{brand, model, serial?}` records what the person confirmed. |
| `GET /api/photos/{sha256}/image` | The photo. |
| `POST /api/photos/{sha256}/vision` | `{}` → `{model, brands, models, serials}` from the `[ai]` vision model; 409 when none is set up, 502 when it fails. |
| `POST /api/photos/{sha256}/delete` | `{}`: remove the photo and its file. |
| `POST /api/batch` | `{text, format: "auto"\|"csv"\|"yaml", fetch, min_score}` → a job (status 202). |
| `POST /api/sync` | `{item?, force?, links_only?, allow_search_links?, min_score?}` → a dry-run job (202). |
| `POST /api/jobs/{id}/attach` | `{}` → an attach job (202) that writes a finished dry run's uploads and links to Homebox. Once per dry run. |
| `GET /api/maintenance?status=new\|filed\|dismissed\|all` | `{suggestions, counts: {new, filed, dismissed}, ai, homebox, target, ready}`. Each suggestion: `id`, `task`, `every` (in words), `every_n` and `every_unit`, `interval` (as the manual said it), `sha256`, `title`, `page`, `quote`, `brand`, `model`, `item_id`, `item_name` (empty when no Homebox item matched), `source` (`rules`, `rules+<model>` when a model reworded it, or `<model>`), `status`, `first_date`, and once filed `scheduled`, `filed_in` (`homebox` or `donetick`) and `homebox_entry` or `donetick_chore`; `error` says why Homebox or Donetick last refused it. `ai` names the `[ai]` model, or is null. `target` is where an approval goes (`[maintenance] target`), `ready` whether it has an address. |
| `POST /api/maintenance/suggest` | `{ai?}` → a `maintenance` job (202) that reads every document and keeps new suggestions. Its rows: `{sha256, brand, model, title, found, added, pages_asked, items, error}`. Reads Homebox's items to match them; writes nothing to Homebox. A `warning` says when Homebox didn't answer. |
| `GET /api/maintenance/items` | Homebox's items (not locations), `[{id, name}]` by name, to choose one for a suggestion with none. |
| `POST /api/maintenance/{id}/approve` | `{task?, scheduled?: "YYYY-MM-DD", item_id?, appliance?}` → the suggestion, now `filed`. With target `homebox`, adds a maintenance entry to the Homebox item; 400 without an item. With target `donetick`, makes a chore "\<appliance\>: \<task\>" that repeats on the suggestion's interval, first due on `scheduled` (`appliance` defaults to the item's name, else the brand and model; no item needed); 400 without Donetick's address or token. 409 when it was decided already, 502 when Homebox or Donetick refuses (the suggestion stays waiting, with the `error`). |
| `POST /api/maintenance/{id}/dismiss` | `{}`: never suggest it again. `/reopen` undoes that. |
| `GET /api/jobs` | Every job since the server started, without rows. |
| `GET /api/jobs/{id}` | `{id, kind, state: running\|done\|failed\|cancelled, total, done, rows, summary, error}`. |
| `POST /api/jobs/{id}/cancel` | `{}`: stop the job after its current row. |

A batch job's rows are `batch.look_up` results (`result` is found, link, weak, none, nosearch or skip), or with
`fetch` they are sync plans (`action` is upload, link, skip or none, and `document` is the downloaded file's
sha256). Starting a job while another runs is refused with 409.

## How it's built

`src/manualbeagle/web/app.py` is a Starlette app. Each endpoint parses its input and calls the code the CLI
uses: `pipeline.find`, `Library`, `batch.look_up` and `batch.row_item`, `sync.plan_item` and `sync.attach`,
and `newitem.preview` and `newitem.add` for Add to Homebox.
Blocking work runs in Starlette's thread pool, and each thread opens its own `Library`, since a SQLite connection
stays in the thread that opened it. `web/jobs.py` runs batch, sync and Add to Homebox in a background thread, one at a time.
`web/static/` is the page: one HTML file, one stylesheet and one script, with no build step and no third-party
JavaScript, plus the web app manifest, its icons and a service worker (`sw.js`, served from `/sw.js` so its scope
is the whole site). The service worker goes to the network first, keeps a copy of the page's own files for when
the server can't be reached, and never stores anything from `/api/`. The Scan button calls `nameplate.py` (OCR,
label patterns, metadata stripping) and, on request, `vision.py`; photos go into `Library`'s `photos` table.
Ask calls `ask.ask` with the `[ai]` model's client. The Maintenance page calls `maintenance.py`: `suggest()`
in a job, `Store` for the list and decisions, and `file_in_homebox()` for the approve button, one approval at
a time. The page puts everything it shows into the document as text, never as HTML, and its Content Security
Policy allows only its own scripts and styles.

The tests are `tests/test_web.py`, `tests/test_web_nameplate.py`, `tests/test_web_maintenance.py` and the web part of `tests/test_ask.py`. They run offline with Starlette's test client, a fake HTTP client, a fake
Homebox, and a real library holding a generated PDF.
