# Running Manual Beagle in Docker

The [Dockerfile](../Dockerfile) builds one image with the web UI, the HTTP API, the CLI, the MCP server and OCR
(Tesseract and OCRmyPDF). Each release is published for linux/amd64 and linux/arm64 as
`ghcr.io/manualbeagle/manualbeagle`, tagged with its version (`0.1.0`), its minor version (`0.1`) and `latest`:

```
docker pull ghcr.io/manualbeagle/manualbeagle:0.1
```

Or build it yourself from a clone:

```
git clone https://github.com/manualbeagle/manualbeagle
cd manualbeagle
docker build -t manualbeagle .
```

The examples below use the name `manualbeagle`; with the published image, write
`ghcr.io/manualbeagle/manualbeagle:0.1` instead.

## Run it

```
docker run -d --name manualbeagle \
  -p 127.0.0.1:8765:8765 \
  -v manualbeagle-data:/data \
  -e MANUALBEAGLE_WEB_TOKEN=<a long random string> \
  manualbeagle
```

Then open `http://127.0.0.1:8765/?token=<the same string>` once in each browser; the token is kept in a cookie.

- **`/data`** holds the library: downloaded PDFs, their page text, the search index, rating plate photos and the
  settings saved on the web UI's Settings page. Keep it on a volume.
- **The token.** Inside a container the server listens on every address, so it always requires a token (see
  [Who can reach it](web.md#who-can-reach-it)). Without `MANUALBEAGLE_WEB_TOKEN` it makes a random one at each
  start and prints a link with it in the container's log. Anyone with the token can read your library and run
  lookups, so keep the port on `127.0.0.1` as above, or put the container behind a TLS reverse proxy and pass
  `--public-url` (below).
- **Behind a reverse proxy:** override the command to add the proxy's address:

  ```
  docker run ... manualbeagle manualbeagle serve --web --host 0.0.0.0 --port 8765 \
    --public-url https://manuals.example.org
  ```

## Configure it

Every setting has a default, and most can be changed on the web UI's Settings page. For the rest, mount a
`config.toml` ([configuration](architecture.md#configuration)) at `/config/config.toml`, or set environment
variables:

| Variable | What it sets |
|---|---|
| `MANUALBEAGLE_SEARXNG` | Your SearXNG's address, for the web-search fallback. The default, `http://localhost:8080`, means the container itself, so set this or turn web search off on the Settings page. |
| `MANUALBEAGLE_HOMEBOX_URL`, `HOMEBOX_TOKEN` | Homebox ([guide](integrations/homebox.md)). |
| `MANUALBEAGLE_HA_URL`, `HA_TOKEN` | Home Assistant ([guide](integrations/homeassistant.md)). |
| `MANUALBEAGLE_PAPERLESS_URL`, `PAPERLESS_TOKEN` | Paperless-ngx ([guide](integrations/paperless.md)). |

Tokens can come from files instead (`token_file` in `config.toml`), which suits Docker secrets. Manual Beagle
reaches a private address only when it's one of these configured services, so `http://homebox:7745` on a Docker
network works once it's set.

## With Docker Compose

```yaml
services:
  manualbeagle:
    image: ghcr.io/manualbeagle/manualbeagle:0.1   # or build: . from a clone
    ports:
      - "127.0.0.1:8765:8765"
    volumes:
      - manualbeagle-data:/data
      # - ./config.toml:/config/config.toml:ro
    environment:
      MANUALBEAGLE_WEB_TOKEN: ${MANUALBEAGLE_WEB_TOKEN}
      # MANUALBEAGLE_SEARXNG: http://searxng:8080
      # MANUALBEAGLE_HOMEBOX_URL: http://homebox:7745
      # HOMEBOX_TOKEN: ${HOMEBOX_TOKEN}
    restart: unless-stopped

volumes:
  manualbeagle-data:
```

SearXNG needs its JSON API turned on (`search: formats: [html, json]` in its `settings.yml`); see
[SearXNG's Docker guide](https://docs.searxng.org/admin/installation-docker.html).

## Use the CLI inside it

```
docker exec -it manualbeagle manualbeagle find LG WM5700HVA
docker exec -it manualbeagle manualbeagle sync homebox --dry-run
```

## What's in the image

Python 3.12 on Debian (the official `python:3.12-slim` image), Manual Beagle with its `web` and `mcp` extras,
Tesseract with English, and OCRmyPDF. It runs as an ordinary user (uid 1000). OCRmyPDF (MPL-2.0) and its
dependencies, and Tesseract (Apache-2.0) and its Debian packages, come under their own licences; Ghostscript isn't
included.
