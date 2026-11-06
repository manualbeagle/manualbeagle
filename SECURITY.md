# Security policy

## Supported versions

Manual Beagle is in early development (0.x) and has no releases yet. Security fixes go to the latest `main`; once
there are releases, the newest 0.x release gets them too.

| Version | Supported |
|---|---|
| `main` | ✓ yes |
| latest 0.x release | ✓ yes (once there are releases) |
| anything older | ✗ no |

## Reporting a vulnerability

**Please don't open a public issue for a security problem.** Report it privately through GitHub's private
vulnerability reporting: on the repository's **Security** tab, choose **Report a vulnerability**. (It will be
turned on when the repository is public.)

Include what you found, how to reproduce it (a command, a config, or a crafted page or PDF), what an attacker
could do with it, and the version or commit you tested.

This is a volunteer project, so these are targets, not guarantees:

- an acknowledgement within 7 days;
- an assessment, and a plan or a request for more detail, within 14 days;
- a fix within 90 days for a confirmed problem, sooner for a serious one, with credit in the changelog and the
  advisory unless you'd rather not be named.

Please give us that time before you publish details.

## What Manual Beagle does to stay safe

Manual Beagle fetches pages and PDFs from sites it doesn't control, including addresses that web search returns,
so it treats everything it downloads as untrusted.

- **Untrusted PDFs.** Adapters and web search point at PDFs on the open web. Manual Beagle extracts their text
  with pypdfium2 (PDFium) and never runs a PDF's JavaScript or forms; it never opens a PDF in a viewer. OCR, when
  installed, runs OCRmyPDF (with Ghostscript and Tesseract) as an external process, only on PDFs that lack text.
  Downloads are stored under their SHA-256 hash, so a server can't choose a file name or path.
- **Size caps.** A page or API response is read into memory only up to 25 MB; a PDF download is streamed to disk
  and stopped at 200 MB.
- **No private addresses.** Every request, and every redirect hop, is refused unless it's http(s) to a public
  address. The only exceptions are the user's own configured services (the SearXNG and Homebox URLs in the
  config, and an Ollama model's address only for the client that talks to it), matched by scheme, host and port.
  Home Assistant and Paperless-ngx are reached only at the addresses set for them. (A DNS answer that changes between the check and the connection is a
  known limit; fetches run on the user's own machine, which limits the harm.)
- **Bot walls stop a lookup.** A CAPTCHA or challenge page raises `Blocked`; nothing tries to get past it.
- **Secrets only from environment variables, files or a command.** Homebox's, Home Assistant's and
  Paperless-ngx's tokens are read from an environment variable, a file or a password manager's command that the
  config names, and Anthropic's API key from an environment variable; never from the config file itself. Each
  token is sent only to its own service, at the address set in config.toml or the environment: an address changed
  on the web UI's Settings page alone never receives it.
- **What goes into an inventory is checked.** `sync` attaches only http(s) links, by default only the maker's
  own, with titles as plain text; uploaded file names are built from the brand, model and kind, not from the
  server.
- **Exports use safe names.** `export` strips characters that aren't allowed in file names.
- **The MCP server reads by default.** Tools that save files exist only with `--allow-write`, and the server has
  no tool that writes to an inventory.

In scope: anything that lets a site, a PDF, a search result or a fixture make Manual Beagle reach a private
address, write outside its folders, leak a secret, run code, or exhaust the user's machine. Out of scope: a maker's
site serving a wrong or outdated manual, and problems in a SearXNG or Homebox server itself (report those to their
projects).
