# A plain folder

No inventory app? Manual Beagle can work from a list and leave the manuals in a folder, named so you can find
them: `Brand/Model - Kind.pdf`.

## Make a list

A YAML list:

```yaml
- {name: Washer, brand: LG, model: WM5700HVA}
- {name: Chest freezer, brand: GE Appliances, model: FCM7SKWW}
- {name: Generator, brand: WEN, model: 56235i}
```

or a CSV file with `brand` and `model` columns (`name` is optional):

```
name,brand,model
Washer,LG,WM5700HVA
Chest freezer,GE Appliances,FCM7SKWW
```

## Find the best manual for each row

```
manualbeagle batch my-things.yaml
```

prints one line per row, starting with a glyph and a word: `✓ found` (a downloadable document scoring at least
`--min-score`, default 60), `▲ link` (only a page for a person to open), `▲ weak` (the best result scored lower),
`✗ none`, `! search` (the web search couldn't run, so "nothing found" isn't known), or `– skip` (no model). Nothing
is downloaded. `--report FILE` saves the results as JSON.

## Download them

```
manualbeagle batch my-things.yaml --fetch
```

makes the same decision for each row as [`sync homebox`](homebox.md#what-it-uploads-and-what-it-links): it
downloads the best PDF whose text names the model (or the maker's own document for it), else keeps the maker's
link. Each PDF goes into the local library, where it's searchable page by page. It ends with coverage by brand:
how many items got a manual (`✓ 3 of 4 with a manual, ▲ 1 maker's link`).

Add `--export DIR` to copy the chosen manuals to a folder as you go:

```
manualbeagle batch my-things.yaml --fetch --export ~/Manuals
```

`DIR` gets `Brand/Model - Kind.pdf` for every row with a manual, and an `index.md` listing every row: `✓ manual`
(the file and where it came from), `▲ link` (the maker's page), `✗ none` or `– skipped` (with the reason).

## Export the whole library

```
manualbeagle export ~/Manuals
```

copies every document in the library to `~/Manuals/Brand/Model - Kind.pdf`, with a small JSON file beside each
PDF (its title, kind, brand, model, page count, source URL and content hash). Run it again at any time: a file
that's already there is kept, and a different document with the same name gets a number (`(2)`).

## Use the library directly

```
manualbeagle docs                              # every document: hash, brand, model, kind, pages
manualbeagle search "door seal"                # full-text search of every page
manualbeagle search "filter" --brand LG        # only one brand's manuals
manualbeagle fetch https://example.com/x.pdf --brand LG --model WM5700HVA   # add one PDF you found yourself
```

The library is in `~/.local/share/manualbeagle` (`%LOCALAPPDATA%\manualbeagle` on Windows, or
`MANUALBEAGLE_HOME`): PDFs under `library/`, named by their content hash so a manual shared by several items is
stored once, and the page index in `manualbeagle.sqlite`. Deleting that folder deletes everything Manual Beagle
has stored.
