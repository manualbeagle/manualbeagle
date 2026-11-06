# MCP: manuals for an AI assistant

`manualbeagle serve --mcp` runs a [Model Context Protocol](https://modelcontextprotocol.io) server over stdio, so
an AI assistant can find manuals and read them, page by page, and cite the page it quotes. It's optional: nothing
else in Manual Beagle needs AI.

## Install

The server needs the optional `mcp` package (the MCP Python SDK, version 2 or newer):

```
pip install "manualbeagle[mcp]"   # from a clone: pip install -e ".[mcp]" (the dev extra includes it)
```

## Connect an assistant

Any MCP client that can start a stdio server works. The command is `manualbeagle serve --mcp`; use the full path
to the `manualbeagle` executable in your virtual environment if it isn't on the client's `PATH`.

Claude Code:

```
claude mcp add manualbeagle -- manualbeagle serve --mcp
```

A client configured with JSON (Claude Desktop and others):

```json
{
  "mcpServers": {
    "manualbeagle": {
      "command": "manualbeagle",
      "args": ["serve", "--mcp"]
    }
  }
}
```

It uses the same `config.toml` as the CLI (web search, library location, the `[ai]` model), so set that up
first.

## Tools

| Tool | What it does | Network |
|---|---|---|
| `find_manual(brand, model, year?, limit?)` | Ranked candidates for a brand and model (add `year` for vehicles): URL, title, kind, score, whether it's link-only, its source, and why it scored as it did. Saves nothing. | The maker's site through its adapter, and your SearXNG |
| `search_manuals(query, brand?, model?, limit?)` | Full-text search of every page of every downloaded manual (SQLite FTS5 syntax). Each hit names its document and page and has a snippet. | None |
| `ask_manuals(question, brand?, model?, document?, pages?)` | A short answer to a question from the downloaded manuals only, citing each page it used as `[n]`: `status` (`answered`, `not_found`, `pages_only`, `model_error`, `no_pages`), `headline`, `answer`, and `sources` (each page's document `sha256`, page number, label, snippet and whether it was `cited`). Written by the `[ai]` model of your config; with none, `status` is `pages_only` and `sources` are the best pages to open with `read_pages`. `not_found` means the manuals don't say. Read-only, so it needs no `--allow-write`. See [Ask your manuals](../web.md#ask-your-manuals). | Your `[ai]` model, if one is set up: Ollama, or Anthropic for Claude |
| `read_pages(document, pages)` | The text of specific pages (up to 20) of a downloaded document, by the `sha256` that `search_manuals` and `list_documents` return. | None |
| `list_documents()` | Every document in the library: brand, model, kind, pages and source URL. | None |
| `maintenance_suggestions(status?, brand?, model?)` | Maintenance tasks read from the manuals: the task, how often, the item, and the document, page and quote it came from. `status` is `new` (waiting for a person, the default), `filed` (added to Homebox), `dismissed` or `all`. Read-only: a person approves suggestions in the web UI. | None |
| `fetch_manual(url, brand, model, title?)` | Only with `--allow-write`. Downloads one manual into the library, indexes its pages, and checks that its text names the model. Refuses a manual mirror's URL (their terms forbid automated downloads), on every redirect too. | The document's host |

## `--allow-write`

By default the server only reads: it can look up candidates and read the library, but it can't save anything.

```
manualbeagle serve --mcp --allow-write
```

adds `fetch_manual`, which downloads a PDF into your library. Turn it on when you want the assistant to build the
library for you. Downloads follow the same rules as the CLI: one request at a time per site, never a private
address, and never past a bot wall. Nothing can be uploaded anywhere: the server has no
Homebox tools.

## Privacy

The server sends nothing anywhere itself beyond the lookups and downloads above, and, for `ask_manuals`, the
question and the text of the pages it picked to your `[ai]` model (with Claude, to Anthropic). But whatever a tool returns
(candidate URLs, the text of manual pages) goes to your assistant, and so to whichever AI provider runs it. See
[privacy.md](../privacy.md).
