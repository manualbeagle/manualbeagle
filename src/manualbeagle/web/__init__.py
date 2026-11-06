"""The web UI: `manualbeagle serve --web`, a small HTTP API and one page, on this computer only by default.

`app.py` is the server (Starlette) and its JSON API, `jobs.py` runs batch and sync in the background, and
`static/` holds the page: plain HTML, CSS and JavaScript with no build step. Needs the optional `web` extra
(`pip install manualbeagle[web]`). See docs/web.md.
"""
