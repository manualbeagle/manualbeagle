# Privacy: what Manual Beagle sends, and where

Manual Beagle runs on your own machine, for you. It has no server, no account, and no telemetry: no analytics,
no crash reports, no update checks. It contacts only the places below, and only when a command you run needs
them.

## The makers' sites

When you look up a model (`find`, `batch`, `sync`, or the MCP `find_manual` tool), each adapter for that brand
asks the maker's own support site for that one model: a search, a product page, or a document list. The site sees
what any website sees from a browser visit: your IP address, the model number you asked about, and the request
headers. The User-Agent names the project and links to its repository
(`ManualBeagle/<version> (+https://github.com/manualbeagle/manualbeagle; ...)`), so a site can see the request came
from Manual Beagle. No browser cookies or logins are used; cookies a site sets last only until the command ends.

When you download a manual (`find --fetch`, `fetch`, `batch --fetch`, `sync`, or the MCP `fetch_manual` tool), the
PDF is fetched from wherever the chosen result points: usually the maker's site or its file host, sometimes
another site that web search found.

Requests go one at a time per site, with a delay between them. Manual Beagle never reaches a private or local
address except the services you configure below.

## Your web search (SearXNG)

When no adapter knows the brand, or the adapters found nothing, Manual Beagle asks the SearXNG at `[search] url`
(default `http://localhost:8080`) up to two queries such as `LG "WM5700HVA" manual pdf`. SearXNG is a search
engine you run yourself; it passes the query on to the search engines it's set up to use (Google, Bing, Brave and
others), which see the query coming from your SearXNG's address.

To never send a search: `engine = "none"` under `[search]` in the config, or `--no-search` on `find`.

## Your Homebox

Only if you configure `[homebox]` and run `homebox` or `sync homebox` commands, or use the web UI's Homebox
parts. Manual Beagle reads your items' names, manufacturers, model numbers, serial numbers, locations and
attachments, and writes attachments (PDFs and links); with `homebox seed`, the items and locations in your list;
and with the web UI's **Add to Homebox**, after you confirm it, the item you scanned with its serial number and
rating-plate photo; and the maintenance entries you approve on the web UI's Maintenance page. The API key is read from the environment variable or file
you name, never from the config file, and is sent only to that Homebox.

Item names and locations stay between Manual Beagle and your Homebox: lookups send only the brand and model.

## Your Home Assistant

Only if you configure `[home_assistant]` and run `ha` commands, or use the web UI's **Import from Home Assistant**.
Manual Beagle signs in with the token you give it and reads three lists: devices (maker, model, name, area, the
integration, whether it's disabled), areas, and config entries (each one's integration). It reads no entity
states, history, camera images, people or settings, and writes nothing to Home Assistant. What it read stays in
memory for that command or page: it isn't saved, except the Homebox items you confirm (a device's name, maker,
model and area, and a note naming the other devices of that model) and a `--report` file you ask for. A lookup
for a device's manual sends the makers' sites only the brand and model.

The token is read from the environment variable, file or command you name, never from the config file, and is
sent only to the address set beside it in the config file or the environment.

## Your Paperless-ngx

Only if you configure `[paperless]` and run `paperless send`, use the web UI's **Send to Paperless…**, or turn on
`auto`. Manual Beagle sends each manual's PDF, a title (brand, model and kind), the brand and model as tags, the
`tags` you set and the document type. Before sending, it asks Paperless whether a document with the same checksum
exists, which sends Paperless the file's SHA-256 and MD5. It reads back only the ids of tags, document types and
documents it looks for, and its upload's task. With `auto` on, every new manual in the library is sent without
asking; nothing else in the library (the page index, photos, Homebox items) is sent. The token is read and bound
to its address as Home Assistant's is.

## The web UI

`manualbeagle serve --web` is a server on your own computer, listening on 127.0.0.1 unless you start it on
another address (which needs a token). It sends nothing anywhere itself: the lookups, downloads and syncs you
start from the page go to the places above, exactly as the same commands do. The page loads nothing from the
internet (no fonts, scripts or analytics); its links to makers' sites open in a new tab without a referrer. A
list you choose for a batch is read in your browser, and only its text goes to the server.

## Rating plate photos

When you scan a rating plate in the web UI, the browser shrinks the photo and redraws it, which leaves behind
the metadata cameras write into photos (the location, the device, the time); the server strips any metadata
that's left before keeping it. It's read on the server with Tesseract, on your own machine, and kept in the
library's `photos/` folder with the brand, model and serial you confirm, until you delete it. The serial number
is never sent anywhere; a lookup sends only the brand and model, as one you typed would.

## AI

No feature needs an AI service, and none is used unless you turn one on (`[ai]` in the config, or the Settings
page). The one model you set up serves three optional features:

- **Reading a rating plate.** The photo goes to the model when you press **Ask the vision model**, or, when the
  model runs on Ollama, by itself when OCR missed the brand or model.
- **Ask your manuals** (the web UI's Ask page, `manualbeagle ask`, the MCP tool `ask_manuals`). The model is sent
  your question, and up to four excerpts of the library's pages that match it (about 1,500 characters each), each
  with its document's brand, model and title and its page number. Nothing else from the library goes with them:
  no other pages, no file names or paths, no photos, no serial numbers, nothing about your inventory. Without a
  model, or with `manualbeagle ask --no-model`, nothing is sent anywhere: the search runs on your machine.
- **Maintenance suggestions**, only when you tick **Also ask** on the Maintenance page or run
  `manualbeagle maintenance suggest --ai`. The model is sent the text of each manual page that names an interval,
  with its document's brand and model, and the tasks the rules found there. Nothing about your inventory goes
  with it. Without it, suggestions are found by rules on your machine.

Where it goes depends on the provider:

- `provider = "ollama"` sends it to the Ollama server you name, usually on your own network.
- `provider = "anthropic"` sends it to Anthropic's API, under Anthropic's terms, with your API key: the photo
  for a rating plate, for Ask your question and the text of those pages, and for maintenance the pages that
  name an interval. The page says so beside the Scan
  button and at the top of Ask.

Nothing else is ever sent to a model. If you connect an AI assistant through [MCP](integrations/mcp.md),
whatever the tools return (candidate links, the text of manual pages) goes to that assistant and its provider,
under their terms.

## What stays on your machine

- The library: downloaded PDFs, their extracted text and the page index, and the rating plate photos you scanned (`~/.local/share/manualbeagle`, or
  `%LOCALAPPDATA%\manualbeagle` on Windows, or `MANUALBEAGLE_HOME`).
- Your config file and any reports you ask for (`--report`), exports (`export`, `--export`) and lists.

Nothing in the library is uploaded anywhere except the PDFs `sync homebox` attaches to your own Homebox, the
rating-plate photos you choose to attach with **Add to Homebox**, the manuals you file in your own
Paperless-ngx, and the maintenance entries (a task, a date, and a quote from the manual) you approve for
Homebox. To remove
everything, delete the library folder.

## Recorded fixtures

Only for contributors: `manualbeagle adapters test <id> --record` saves the responses of a maker's site to
`tests/fixtures/`, to be committed to the repository. They hold the site's public pages and API answers for the
test models, never a PDF's body. Check them before committing, as you would any file.
