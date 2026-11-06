## What this changes

<!-- One or two sentences. For an adapter: the brand, how it searches, and how many models pass live. -->

## Checklist

- [ ] `ruff check src tests` and `ruff format --check src tests` pass.
- [ ] `pytest` passes (offline; no test touches the network unless it's marked `live`).
- [ ] Docs are updated for anything a user or contributor would notice (`README.md`, `CONTRIBUTING.md`,
      `docs/architecture.md`, `docs/adapters.md`, `docs/integrations/`), and public code has docstrings and type
      hints.
- [ ] Colour in any output is paired with a glyph or a word.
- [ ] No new dependency, or it's compatible with Apache-2.0 and the description says why it's needed.

### For a new or changed adapter

- [ ] The adapter starts with its header: the site, how it searches, known gaps, and today's date as
      "Last verified".
- [ ] At least two real test cases, with fixtures recorded by `manualbeagle adapters test <id> --record -v`.
- [ ] The fixtures are small and hold no PDF bodies.
- [ ] `docs/brands.md` regenerated with `manualbeagle adapters scoreboard --write docs/brands.md`.
- [ ] The output of `manualbeagle adapters test <id> --live -v` is below.

### Fetching rules

- [ ] Every request goes through `ctx.http` (the polite client): one request at a time per site, the project's
      User-Agent.
- [ ] Nothing crawls or lists a site in bulk: only what one lookup needs.
- [ ] Nothing gets around a CAPTCHA, a bot wall or a login (no browser automation, spoofed headers, copied
      cookies or proxies).
- [ ] The site's terms don't forbid automated access.
- [ ] Nothing hosts, shares or mirrors PDFs.

## Live test output (adapters)

```
```
