# Homebox

[Homebox](https://github.com/sysadminsmedia/homebox) keeps your items, their locations, warranties and
maintenance. Manual Beagle is a companion to it: it reads the items that have a manufacturer and a model number,
finds each one's manual, and attaches it to the item, as a PDF when it can and as a link to the maker's page when
it can't. It doesn't replace anything Homebox does.

It talks to Homebox v0.27 or newer through Homebox's own API.

## Set up

1. **Make an API key.** In Homebox, open your profile page and create an API key. A Homebox user of its own for
   Manual Beagle makes its attachments easy to tell apart, but your own account works too.
2. **Give the key to Manual Beagle** through an environment variable (default `HOMEBOX_TOKEN`), a file, or a
   command that prints it. The key never goes in the config file itself.

   ```
   export HOMEBOX_TOKEN=...            # PowerShell: $env:HOMEBOX_TOKEN = "..."
   ```

3. **Point the config at your Homebox.** In `config.toml` (`~/.config/manualbeagle/config.toml`, or
   `%APPDATA%\manualbeagle\config.toml` on Windows; see [architecture.md](../architecture.md#configuration)):

   ```toml
   [homebox]
   url = "http://homebox.example:7745"   # your Homebox's address
   token_env = "HOMEBOX_TOKEN"           # the environment variable holding the API key
   token_file = ""                       # or a file holding it
   token_command = []                    # or a command that prints it (a list of arguments, or one string)
   attach_links = true                   # false: sync never attaches a maker's link, only PDFs
   ```

   They're tried in that order: the environment variable, then the file, then the command. In a file or a
   command's output, a `token=...` line wins (so a file can hold `url=` and `token=` lines); otherwise the first
   line that isn't a `#` comment is the key, and an upper-case `NAME=value` line gives its value.

   `token_command` suits a password manager's command line, or a helper that decrypts a key file. It runs
   without a shell; `token_command_env` adds environment variables for it. A command that fails or prints
   nothing counts as no key, and its output is never shown. For example:

   ```toml
   token_command = ["op", "read", "op://Private/Homebox/manualbeagle-token"]   # 1Password's CLI
   token_command = ["pass", "show", "homebox/manualbeagle"]                    # pass
   token_command = ["python", "decrypt_key.py", "homebox"]                     # your own helper
   token_command_env = { DECRYPT_TO_STDOUT = "1" }
   ```

   `MANUALBEAGLE_HOMEBOX_URL` overrides `url`. The web UI's Settings page can change the address too, but the
   key is only ever sent to the address set beside it in the config file or the environment (the same scheme,
   host and port): an address changed on the page alone gets no key, so whoever can change settings can't send
   your key to their own server. Your Homebox may be on a private address: Manual Beagle refuses
   private addresses for everything except the services you configure, matched by scheme, host and port.

4. **Check it:**

   ```
   manualbeagle homebox status
   ```

   prints `✓ Homebox <version> at <url>`, or `✗` and the error.

## Look at your items

```
manualbeagle homebox items
```

lists every item with its manufacturer, model and location; `✓` marks an item that already has a manual and `·`
one that doesn't. Only items with both a manufacturer and a model number can be matched, so fill those in first.

## Fill Homebox from a list

If your Homebox is empty, `homebox seed` adds items from a YAML list:

```yaml
- {name: Washer, brand: LG, model: WM5700HVA, location: Laundry}
- {name: Chest freezer, brand: GE Appliances, model: FCM7SKWW, location: Utility room}
- {name: Thermostat, brand: Resideo, model: RCHT9610WF, notes: Upstairs hallway}
```

```
manualbeagle homebox seed items.yaml --dry-run    # what it would add
manualbeagle homebox seed items.yaml
```

Missing locations are created. An item with the same name and model as an existing one is skipped, so the file can
be run again. Each line starts with a glyph and a word: `+ would add`, `✓ added`, `– exists`.

## Sync manuals

Always start with a dry run:

```
manualbeagle sync homebox --dry-run --report sync.json
```

For each item it prints what it would do, and the dry run writes nothing to Homebox. It does download and check
the PDFs, into your local library (so the real run is quick and the manuals are searchable straight away).
`--report` saves the decisions as JSON. The summary line counts `✓ upload`, `▲ link`, `– skipped` and
`✗ not found`. When it looks right, run it for real:

```
manualbeagle sync homebox
```

### What it uploads and what it links

For each item with a manufacturer and a model number and no manual yet:

1. It finds candidates as `manualbeagle find` does: the brand's adapters first, then web search if they found
   nothing.
2. It tries the top three candidates scoring at least `--min-score` (default 60), downloading each PDF and reading
   its text. The first whose text names the model is **uploaded** to the item as a `manual` attachment, named
   `<Brand> <Model> - <Kind>.pdf`. An adapter's own result from the maker's site may also be uploaded when its text
   names a related model (a longer SKU, or the base tool of a kit), or when the adapter says the maker's manuals
   cover a whole range (`family_manuals`). A PDF found by web search is uploaded only if its text names the model.
3. If none passes this time, a manual an earlier run downloaded for the same brand and model is uploaded, if its
   text names the model ("kept from an earlier run"). Web search answers differently from one run to the next, so
   this keeps a manual once found.
4. If no PDF passes, the best candidate is attached as a **link** (an external `manual` attachment with a plain-text
   title): the maker's own page, meaning an adapter's result or a URL on the maker's domains. Links found by web
   search on other sites are attached only with `--allow-search-links`.
5. Otherwise the item is left alone and the line says why.

Items without a manufacturer or model, and items that already have a manual, are skipped.

### Options

| Option | What it does |
|---|---|
| `--dry-run` | Find and check everything; write nothing to Homebox. |
| `--report FILE` | Save every item's decision as JSON. |
| `--item TEXT` | Only items whose name contains this. |
| `--links-only` | Attach the maker's link; never upload a PDF. |
| `--min-score N` | The lowest score a candidate needs (default 60). `manualbeagle find --why` shows how scores are made. |
| `--force` | Also items that already have a manual. |
| `--allow-search-links` | Also attach links found by web search, not only the maker's. |
| `--metrics FILE` | Also write the outcome as a Prometheus textfile, for node_exporter (see below). |

With `attach_links = false` in `[homebox]`, sync uploads PDFs but attaches no links: a link it would have
attached shows `– not attached` instead.

`sync homebox` exits with 1 when Homebox can't be read or refuses a write, so a scheduled run that went wrong
shows as a failed job; otherwise 0, whatever it found.

### Every night

Items you add to Homebox by hand get their manuals by themselves if `sync homebox` runs on a schedule. Items that
already have a manual are skipped, so a nightly run only does the new ones. With systemd, for example:

```ini
# /etc/systemd/system/manualbeagle-sync.service
[Unit]
Description=Manual Beagle: attach manuals to new Homebox items
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=manualbeagle
EnvironmentFile=/etc/manualbeagle/homebox.env
ExecStart=/opt/manualbeagle/bin/manualbeagle sync homebox --report /var/lib/manualbeagle/sync.json \
    --metrics /var/lib/prometheus/node-exporter/manualbeagle_sync.prom
```

```ini
# /etc/systemd/system/manualbeagle-sync.timer
[Unit]
Description=Run manualbeagle-sync nightly

[Timer]
OnCalendar=*-*-* 03:20
RandomizedDelaySec=5m
Persistent=true

[Install]
WantedBy=timers.target
```

`--metrics` writes, for node_exporter's textfile collector (the file is replaced whole, never half-written):

| Metric | What it says |
|---|---|
| `manualbeagle_sync_last_run_timestamp_seconds` | When the run finished. Alert when it's more than a day or so old. |
| `manualbeagle_sync_success` | 1 if Homebox was read and accepted every write tried, else 0 (also written when Homebox can't be reached). |
| `manualbeagle_sync_items{action}` | Items by decision: `upload`, `link`, `skip` (no model, or a manual already) and `none` (nothing found). |
| `manualbeagle_sync_attached` | Manuals written to Homebox this run. |
| `manualbeagle_sync_refused` | Writes Homebox refused. |
| `manualbeagle_sync_duration_seconds`, `manualbeagle_sync_dry_run` | How long it took, and whether it was a dry run. |

## Add a scanned appliance

The web UI can add an appliance to Homebox straight from a photo of its rating plate: scan it on the Find page,
confirm the brand and model, then **Add to Homebox…**. Manual Beagle suggests a name, lets you pick a location,
and checks Homebox for an item that already has the same maker and model number, offering to use that one
instead of making a duplicate. It writes nothing until you've reviewed the list of writes and pressed **Write to
Homebox**. Then it creates the item (manufacturer, model number, serial number, location), attaches the plate
photo as the item's picture, and finds and attaches the manual by the rule above. See
[web.md](../web.md#add-it-to-homebox).

## Maintenance from the manuals

Manuals say how often to clean, check or replace things. The web UI's **Maintenance** page reads every manual in
the library for those tasks and suggests them for the matching items; each one you approve becomes a Homebox
maintenance entry with a scheduled date, and Homebox's notifier reminds you on that day (set up a notifier under
your Homebox profile). The page and how suggestions are found are in [the web UI guide](../web.md#maintenance).

```
manualbeagle maintenance suggest            # read the manuals (rules only); --ai to ask the [ai] model too
manualbeagle maintenance list               # what's waiting; --status filed|dismissed|all, --json
```

Then approve or dismiss each suggestion on the Maintenance page. Nothing is written to Homebox until you do.

An approved suggestion is sent as `POST /api/v1/entities/{id}/maintenance` with:

| Field | Value |
|---|---|
| `name` | The task, as you left it ("Clean the air filter"), at most 255 characters. |
| `scheduledDate` | The first date you chose, `YYYY-MM-DD` (default: one interval from today). |
| `description` | "Repeat every 2 weeks. From \<manual\> (\<brand\> \<model\>), page N: "\<quote\>"", and a note that Manual Beagle suggested it and a person approved it. |
| `cost` | `0`. |

Homebox (v0.27) has no repeating maintenance: when you mark an entry done, add the next one. The description
says how often.

## What Homebox sees

Manual Beagle reads your items (names, manufacturers, model numbers, serial numbers, locations and attachments)
and writes attachments, maintenance entries you approve, the items and locations `homebox seed` creates, and the item, serial number and
plate photo **Add to Homebox** writes once you've confirmed it. See [privacy.md](../privacy.md).
