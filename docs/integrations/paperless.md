# Paperless-ngx

[Paperless-ngx](https://docs.paperless-ngx.com/) keeps your scanned and downloaded documents, OCRed and
searchable. Manual Beagle can file the manuals in its library there too, so they sit beside your receipts and
warranties: each one with a title, a tag for its brand and one for its model, a `manual` tag and the **Manual**
document type, and never twice.

It's optional, and off until you set it up. Manual Beagle's own library keeps every manual either way.

## What it sends

For each manual it files, Manual Beagle:

1. **Checks Paperless doesn't have the file already**, by its checksum (`GET /api/documents/?checksum__iexact=`),
   when the token may view documents (see *Set up*).
   Paperless-ngx 3 stores the original file's SHA-256 and Paperless-ngx 2 its MD5, so both are asked. Paperless 3
   accepts a duplicate unless `PAPERLESS_CONSUMER_DELETE_DUPLICATES` is set, which is why Manual Beagle checks
   first, and also remembers what it sent (the `paperless` table in its database).
2. **Finds or makes the tags and the document type** by name: the brand ("LG"), the model ("WM4000HWA"), each name
   in `tags` (default `manual`), and `document_type` (default "Manual"). Ones it makes have matching set to
   **None**, so Paperless never puts them on other documents by itself.
3. **Uploads the PDF** (`POST /api/documents/post_document/`) with the title `Brand Model - Kind`, such as
   `LG WM4000HWA - Owner's manual`, the tags and the type. Paperless then consumes it in the background.
4. **Follows the consumption task** (`GET /api/tasks/?task_id=`) until Paperless says it's filed, or later: a
   manual still being consumed shows as `… queued`, and the next `paperless status` or send looks again.

A manual counts as sent once it's filed, or found in Paperless already. One that failed, and one whose file
changed since it was sent (OCR rewrites a file), is offered again.

**Paperless's own rules still run.** A workflow that runs on "Document added" may change the title or the tags
Manual Beagle set, for example one that asks an AI to retitle documents. Give such a workflow a filter that leaves
out documents tagged `manual` (or whatever is in `tags`) if you want Manual Beagle's titles kept.

## Set up

1. **Make an API token.** In Paperless, open your profile (the person icon, **My Profile**) and create an **API
   auth token**. A Paperless user of its own for Manual Beagle keeps its uploads apart and can be limited: it
   needs to add documents, view and add tags, view and add document types, and view tasks (to see when an upload
   is filed). Documents it uploads are owned by it; give the people who should see them permission, or make them
   visible to everyone.

   **"View documents" is optional, and often better left off.** With it, Manual Beagle checks each manual's
   checksum against Paperless before uploading. But a user who may view documents sees every document it owns
   and every document *nobody* owns, and documents Paperless consumes from a folder have no owner, so the token
   could read your other paper. Without it, the checksum check answers 403, and Manual Beagle relies on its own
   record of what it sent (the `paperless` table, kept with the library) not to send a manual twice.
2. **Give the token to Manual Beagle** through an environment variable (default `PAPERLESS_TOKEN`), a file, or a
   command that prints it, the same ways as [Homebox's key](homebox.md#set-up). The token never goes in the
   config file.
3. **Point the config at Paperless:**

   ```toml
   [paperless]
   url = "http://paperless.local:8000"   # Paperless's address, as a browser opens it
   token_env = "PAPERLESS_TOKEN"         # the environment variable holding the token
   token_file = ""                       # or a file holding it
   token_command = []                    # or a command that prints it
   auto = false                          # true: file every new manual by itself
   document_type = "Manual"              # made when missing; "" sets none
   tags = ["manual"]                     # besides the brand and model tags
   ```

   `MANUALBEAGLE_PAPERLESS_URL` overrides `url`. The web UI's Settings page can change the address, `auto` and
   the document type, but the token is only ever sent to the address set beside it in the config file or the
   environment: an address changed on the page alone gets no token.
4. **Check it:**

   ```
   manualbeagle paperless status
   ```

   prints `✓ Paperless-ngx <version> at <url>` and how many manuals are filed, queued, failed and not sent yet.

## Send manuals

Look first, then send:

```
manualbeagle paperless send --dry-run             # every manual Paperless doesn't have yet
manualbeagle paperless send WM4000 --dry-run      # only those whose brand, model or title contains WM4000
manualbeagle paperless send WM4000                # asks, then sends, and waits for Paperless to file it
```

It asks before sending (`[y]es, [n]o, [e]ach`); without a terminal, nothing is sent unless you pass `--yes`.
`MATCH` may also be the start of a library document's SHA-256. For one manual it waits up to two minutes for
Paperless to file it; for several it sends them and leaves them queued (`--wait SECONDS` changes either).
`--again` offers manuals sent before as well, though one Paperless has is still never uploaded twice.

### Every new manual, by itself

With `auto = true`, every manual that reaches the library is filed without asking:

- after a CLI command that downloads (`find --fetch`, `fetch`, `batch --fetch`, `sync homebox`), when it ends;
- in the web UI, by a background thread that looks every five minutes, and soon after the **Fetch** button, while
  no batch or sync is running.

Turn it on only once a dry run shows what it would send.

## In the web UI

The **Library** page shows Paperless's state and **Send N to Paperless…**, which asks, then sends every manual it
doesn't have. A manual's page in the reader has **Send to Paperless…** for that one. With `auto` on, the Library
page says new manuals are filed by themselves instead. The HTTP API is in
[web.md](../web.md#home-assistant-and-paperless-ngx).

## Code

`src/manualbeagle/sinks/paperless.py`: the client (`Paperless`), `file_document()`, `Ledger` (the `paperless`
table), `check_queued()` and `sweep()`, which `auto` runs. `web/integrations.py` has the endpoints and
`PaperlessAuto`. The tests use a fake Paperless in `tests/test_paperless.py`.
