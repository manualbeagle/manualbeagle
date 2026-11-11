"""manualbeagle: the command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .adapters import Registry
from .config import Config, load, read_file
from .http import FetchError
from .models import KIND_LABELS, DocKind, Query
from .search import make_engine


def _out(s: str = "") -> None:
    sys.stdout.write(s + "\n")


def _registry(cfg: Config) -> Registry:
    return Registry(cfg.adapter_dirs)


def cmd_find(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle find BRAND MODEL`: print the ranked candidates; with --fetch, download the best."""
    from .pipeline import find

    engine = None if args.no_search else make_engine(cfg.search.engine, url=args.searxng or cfg.search.url)
    q = Query(
        brand=args.brand, model=args.model, year=args.year, kind=DocKind(args.kind) if args.kind else None
    )
    with cfg.client() as http:
        res = find(q, _registry(cfg), http, engine, always_search=args.search, only_adapter=args.adapter)
        if args.json:
            _out(res.model_dump_json(indent=1))
            return 0 if res.candidates else 1
        nq = res.query
        brand = nq.canonical_brand or nq.brand
        _out(
            f"{brand} {nq.model}"
            + (f" ({nq.year})" if nq.year else "")
            + f"   variants: {', '.join(nq.variants)}"
        )
        for r in res.adapters:
            state = "✓ ok" if r.ok else ("✗ blocked" if r.blocked else "✗ error")
            _out(f"  adapter {r.adapter}: {state}, {r.count} found" + (f" — {r.error}" if r.error else ""))
        if not res.adapters:
            _out("  no adapter for this brand")
        if res.searched:
            _out(f"  web search: {'✗ ' + res.search_error if res.search_error else '✓ ok'}")
        if not res.candidates:
            _out("\nNothing found.")
            return 1
        _out("")
        for i, c in enumerate(res.candidates[: args.limit], 1):
            mark = "★" if i == 1 else " "
            kind = KIND_LABELS.get(c.kind, "?") if c.kind else "?"
            link = "  [link only: open it yourself]" if c.link_only else ""
            _out(f"{mark}{i:>2}. {c.score:5.0f}  {kind:<18} {c.title[:70]}{link}")
            _out(f"         {c.url}")
            if args.why:
                _out(f"         why: {'; '.join(c.reasons)}")
        if args.fetch:
            from .library import Library

            lib = Library(cfg.library, cfg.db_path)
            for c in res.candidates:
                if c.link_only:
                    continue
                try:
                    doc = lib.fetch(c, nq, http)
                except FetchError as e:
                    _out(f"\n✗ fetch failed: {e}")
                    continue
                _out(f"\n✓ saved {doc.path} ({doc.page_count} pages{', OCR' if doc.ocr else ''})")
                _out(f"  check: {'; '.join(doc.check_reasons or [])}")
                break
            lib.close()
    return 0


def cmd_adapters_list(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle adapters list`: one line per adapter with its status as a glyph and a word."""
    glyph = {
        "works": "✓ works",
        "partial": "▲ partial",
        "blocked": "✗ blocked",
        "link": "▲ link",
        "untested": "? untested",
    }
    for a in sorted(_registry(cfg), key=lambda a: a.brand.lower()):
        s = a.spec
        _out(
            f"{a.id:<22} {s.brand:<24} {glyph[s.status]:<12} {len(s.tests)} tests   {', '.join(s.domains[:2])}"
        )
    return 0


def cmd_adapters_test(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle adapters test [IDS]`: run test cases offline (default), live, or live while recording."""
    from .testing import FIXTURES, run_case

    reg = _registry(cfg)
    ids = args.ids or [a.id for a in reg]
    mode = "record" if args.record else ("live" if args.live else "replay")
    fixtures = Path(args.fixtures) if args.fixtures else FIXTURES
    failed = 0
    results = []
    for aid in ids:
        a = reg.get(aid)
        for case in a.spec.tests:
            r = run_case(a, case, mode=mode, fixtures=fixtures, delay=cfg.delay, check_text=args.check_text)
            results.append(r.as_dict())
            _out(f"{r.glyph} {r.status:<10} {aid:<20} {case.model:<22} {r.message}")
            if args.verbose and r.status != "pass":
                for c in r.top:
                    _out(
                        f"      {c.score:4.0f} {c.kind.value if c.kind else '?':<16} {c.title[:50]}  {c.url}"
                    )
            if r.status in ("fail", "error"):
                failed += 1
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    return 1 if failed else 0


def cmd_scoreboard(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle adapters scoreboard`: print the brands table, or write it with --write."""
    from .scoreboard import build, load_results

    live = load_results(args.live) if args.live else None
    text = build(_registry(cfg), live=live, live_when=args.live_when or "")
    if args.write:
        Path(args.write).write_bytes(text.encode("utf-8"))
        _out(f"✓ wrote {args.write}")
    else:
        _out(text)
    return 0


def cmd_adapters_report(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle adapters report RESULTS`: the Markdown breakage report of a live run; exit 1 if any broke."""
    from .scoreboard import breakage, load_results

    text, broken = breakage(load_results(args.results), _registry(cfg), run_url=args.run_url or "")
    if args.write:
        Path(args.write).write_bytes(text.encode("utf-8"))
        _out(f"{'✗' if broken else '✓'} {broken} broken; wrote {args.write}")
    else:
        _out(text)
    return 1 if broken else 0


def cmd_batch(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle batch FILE`: the best manual for every row of a list (see batch.py)."""
    from .batch import run

    if args.export and not args.fetch:
        _out("✗ --export needs --fetch")
        return 2
    return run(Path(args.file), cfg, args.min_score, args.report, fetch=args.fetch, export=args.export)


def cmd_export(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle export DEST`: copy the library to DEST as Brand/Model - Kind.pdf."""
    from .library import Library
    from .sinks.folder import export

    lib = Library(cfg.library, cfg.db_path)
    files = export(lib, Path(args.dest))
    lib.close()
    _out(f"✓ {len(files)} documents in {args.dest}")
    return 0


def cmd_fetch(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle fetch URL --brand B --model M`: download one PDF into the library and index it.

    Refuses a manual mirror's URL (its terms forbid automated downloads), on every redirect hop, since a
    typed URL skips the ranking that would mark it link-only.
    """
    from .library import Library
    from .models import Candidate
    from .normalize import normalise

    reg = _registry(cfg)
    if refusal := reg.download_refusal(args.url):
        _out(f"✗ not downloaded: {refusal}")
        return 1
    lib = Library(cfg.library, cfg.db_path)
    q = normalise(Query(brand=args.brand, model=args.model))
    with cfg.client(refuse=reg.download_refusal) as http:
        try:
            doc = lib.fetch(Candidate(url=args.url, title=args.title or "", source="user"), q, http)
        except FetchError as e:
            _out(f"✗ {e}")
            lib.close()
            return 1
    _out(
        f"✓ {doc.path} ({doc.page_count} pages{', OCR' if doc.ocr else ''}); {'; '.join(doc.check_reasons or [])}"
    )
    lib.close()
    return 0


def cmd_search(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle search QUERY`: full-text search of the library, one line per matching page."""
    from .library import Library

    lib = Library(cfg.library, cfg.db_path)
    rows = lib.search(args.query, brand=args.brand, model=args.model, limit=args.limit)
    for r in rows:
        _out(f"{r['brand']} {r['model']} · {r['title'] or r['kind'] or ''} · p. {r['page']}")
        _out(f"    {' '.join(r['snip'].split())}")
    if not rows:
        _out("No matches.")
    lib.close()
    return 0 if rows else 1


def cmd_ask(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle ask QUESTION`: a short answer from the library's pages, citing each page it used.

    Uses the `[ai]` model when one is set up (and not `--no-model`); otherwise prints the best pages. Exits 1
    when no page matches the question or the manuals don't say.
    """
    from . import ask
    from .library import Library

    lib = Library(cfg.library, cfg.db_path)
    try:
        with ask.model_client(cfg) as http:
            try:
                model = None if args.no_model else ask.make_model(cfg.ai, http)
            except ask.AskError as e:
                _out(f"✗ {e}")
                return 2
            a = ask.ask(lib, args.question, model, args.brand, args.model, args.document, args.pages)
    finally:
        lib.close()
    if args.json:
        _out(json.dumps(a.to_dict(), indent=1, ensure_ascii=False))
        return 0 if a.status not in ("no_pages", "not_found") else 1
    _out(a.headline + (f"  ({a.model})" if a.model else ""))
    if a.answer:
        _out("\n" + a.answer)
    if a.sources:
        _out("\nPages:" if a.status == "answered" else "\nThe best pages:")
    for s in a.sources:
        mark = "✓" if s.cited else ("○" if a.status == "answered" else " ")
        _out(f" {mark} [{s.n}] {s.label}  (document {s.sha256[:10]})")
        if a.status != "answered":
            _out(f"       {s.snip}")
    return 0 if a.status not in ("no_pages", "not_found") else 1


def cmd_ocr(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle ocr`: OCR library documents that were indexed without text, once OCR is installed."""
    from .library import Library, ocr_available

    if not ocr_available():
        _out("✗ OCR isn't installed: it needs OCRmyPDF (pip install ocrmypdf) and Tesseract")
        return 2
    lib = Library(cfg.library, cfg.db_path)
    done = lib.ocr_missing()
    lib.close()
    for d in done:
        if d["pages_with_text"] is None:
            _out(f"✗ failed: {d['brand']} {d['model']}  {d['path']}")
        else:
            n = d["pages_with_text"]
            _out(f"✓ OCR: {d['brand']} {d['model']}, {n} page{'' if n == 1 else 's'} with text  {d['path']}")
    if not done:
        _out("– nothing to do: every document has text or was OCRed already")
    return 1 if any(d["pages_with_text"] is None for d in done) else 0


def cmd_docs(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle docs`: list the documents in the library."""
    from .library import Library

    lib = Library(cfg.library, cfg.db_path)
    for d in lib.documents():
        _out(
            f"{d['sha256'][:10]}  {d['brand']} {d['model']}  {d['kind'] or '?'}  {d['pages']} pp  {d['title'] or ''}"
        )
    lib.close()
    return 0


def cmd_sync(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle sync homebox`: find and attach manuals for Homebox items (see sync.py)."""
    from .sync import sync

    return sync(args, cfg)


def cmd_homebox(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle homebox status|items|seed`: look at a Homebox, or add items to it from a list."""
    from .sinks.homebox import HomeboxError
    from .sync import homebox

    try:
        hb = homebox(cfg)
        if args.hcmd == "status":
            st = hb.status()
            _out(f"✓ Homebox {st.get('build', {}).get('version', '?')} at {cfg.homebox.url}")
        elif args.hcmd == "items":
            for i in hb.items():
                mark = "✓" if i.manuals else "·"
                _out(f"{mark} {i.name[:36]:<36} {i.manufacturer:<20} {i.model:<20} {i.location}")
        elif args.hcmd == "seed":
            from .sinks.seed import seed

            return seed(hb, Path(args.file), args.dry_run)
        hb.close()
    except HomeboxError as e:
        _out(f"✗ {e}")
        return 1
    return 0


def cmd_maintenance(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle maintenance suggest | list`: read manuals for maintenance tasks, or list the suggestions.

    Approving a suggestion (filing it in Homebox or Donetick, `[maintenance] target`) is done by a person on the web
    UI's Maintenance page.
    """
    from . import maintenance as mt
    from .library import Library

    lib = Library(cfg.library, cfg.db_path)
    try:
        store = mt.Store(lib)
        if args.mcmd == "list":
            rows = store.list(None if args.status == "all" else args.status)
            if args.json:
                _out(json.dumps([s.public() for s in rows], indent=1, ensure_ascii=False))
                return 0
            glyph = {"new": "● new", "filed": "✓ filed", "dismissed": "– dismissed"}
            for s in rows:
                who = s.item_name or f"{s.brand} {s.model} (no Homebox item)"
                _out(f"{glyph[s.status]:<11} #{s.id:<4} {who}: {s.task}, {s.every.label}")
                _out(f"            {s.title or s.brand + ' ' + s.model}, page {s.page}: “{s.quote}”")
            n = store.counts()
            _out(f"\n● {n['new']} waiting, ✓ {n['filed']} filed, – {n['dismissed']} dismissed")
            return 0
        items, ask, label = None, None, ""
        if cfg.homebox.url and not args.no_homebox:
            from .sinks.homebox import HomeboxError
            from .sync import homebox

            try:
                hb = homebox(cfg)
                items = hb.items()
                hb.close()
            except (HomeboxError, OSError) as e:
                _out(f"▲ Homebox: {e}; suggestions are kept by brand and model instead")
        client = None
        if args.ai:
            label = mt.model_label(cfg.ai)
            if not label:
                _out("✗ --ai needs a model in the config's [ai] table (provider ollama or anthropic)")
                return 2
            from .ask import model_client

            client = model_client(cfg)
            http = client

            def ask(prompt: str) -> str:
                return mt.ask_model(cfg.ai, http, prompt)

        sha = None
        if args.document:
            hits = [d["sha256"] for d in lib.documents() if d["sha256"].startswith(args.document.lower())]
            if len(hits) != 1:
                _out(f"✗ {len(hits)} documents start with {args.document}")
                return 2
            sha = hits[0]
        found = added = docs = 0
        try:
            for c in mt.suggest(lib, items, ask, label, sha=sha):
                docs += 1
                found += c.found
                added += c.added
                if c.found or c.error:
                    where = f" → {', '.join(c.items)}" if c.items else ""
                    err = f"  ▲ model: {c.error}" if c.error else ""
                    _out(
                        f"{'✓' if c.found else '–'} {c.brand} {c.model}: {c.found} found, {c.added} new{where}{err}"
                    )
        finally:
            if client is not None:
                client.close()
        _out(
            f"\n✓ {added} new suggestion{'' if added == 1 else 's'} ({found} found in {docs} documents"
            f"{', with ' + label if label else ', rules only'}). Approve them on the web UI's Maintenance page."
        )
        return 0
    finally:
        lib.close()


def cmd_nameplate(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle nameplate PHOTO`: read a rating plate photo and suggest the brand, model and serial."""
    from .nameplate import brand_names, image_type, ocr_available, ocr_image, read_plate

    data = Path(args.photo).read_bytes()
    if image_type(data) is None:
        _out("✗ not a JPEG or PNG photo")
        return 2
    if not ocr_available():
        _out("✗ reading photos needs Tesseract: see docs/web.md")
        return 2
    reading = read_plate(ocr_image(data), brand_names(n for a in _registry(cfg) for n in a.names()))
    if args.json:
        _out(json.dumps(reading.as_dict(), indent=1, ensure_ascii=False))
        return 0
    for label, found in (("brand", reading.brands), ("model", reading.models), ("serial", reading.serials)):
        if not found:
            _out(f"– {label}: not found")
        for s in found:
            _out(f"{'✓' if s.sure else '▲'} {label}: {s.value}  ({s.why})")
    if args.text:
        _out("\n" + reading.text)
    return 0 if reading.models else 1


def _confirm(question: str, yes: bool) -> str:
    """Ask before writing: "y", "n" or "e" (each, one at a time). `yes` (--yes) answers "y" without asking.

    Without a terminal to ask on and without --yes, the answer is "n": nothing is written unasked.
    """
    if yes:
        return "y"
    if not sys.stdin.isatty():
        _out("✗ not written: run it in a terminal to confirm, or pass --yes after reviewing the dry run")
        return "n"
    answer = input(f"{question} [y]es, [n]o, [e]ach: ").strip().lower()[:1]
    return answer if answer in ("y", "e") else "n"


def cmd_ha(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle ha status|devices|import`: read Home Assistant's devices; offer them as Homebox items."""
    from .sinks.homebox import HomeboxError
    from .sources.homeassistant import (
        HomeAssistantError,
        add_to_homebox,
        from_config,
        plan_import,
        registry_brands,
        summary,
    )
    from .sync import homebox

    try:
        ha = from_config(cfg)
        if args.hacmd == "status":
            _out(f"✓ Home Assistant {ha.status() or '?'} at {cfg.home_assistant.url}")
            return 0
        devices = ha.devices()
    except HomeAssistantError as e:
        _out(f"✗ Home Assistant: {e}")
        return 1
    hb = None
    items: list = []
    try:
        hb = homebox(cfg)
        items = hb.items()
    except (HomeboxError, OSError) as e:
        if args.hacmd == "import" and not args.dry_run:
            _out(f"✗ Homebox: {e}")
            return 1
        _out(f"– Homebox isn't set up ({e}); every device with a maker and model counts as new")
    reg = _registry(cfg)
    offers = plan_import(devices, items, cfg.home_assistant.skip_integrations, registry_brands(reg))
    if args.only:
        offers = [o for o in offers if args.only.lower() in o.device.name.lower()]
    glyphs = {"new": "+ new", "have": "✓ have", "skip": "– skip"}
    shown = [o for o in offers if args.all or o.action != "skip"] if args.hacmd == "devices" else offers
    for o in shown:
        what = f"{o.brand} {o.model}".strip() or "(no maker or model)"
        _out(
            f"{glyphs[o.action]:<7} {o.device.name[:34]:<34} {what[:36]:<36} {o.location or '-':<16} {o.reason}"
        )
    if args.hacmd == "devices" and not args.all:
        hidden = sum(1 for o in offers if o.action == "skip")
        if hidden:
            _out(f"        ({hidden} skipped devices not shown: --all lists them)")
    if getattr(args, "find", False):
        from .batch import look_up

        engine = make_engine(cfg.search.engine, url=cfg.search.url)
        _out("\nManuals (looked up, nothing downloaded):")
        with cfg.client() as http:
            for o in offers:
                if o.action == "skip":
                    continue
                r = look_up({"name": o.device.name, "brand": o.brand, "model": o.model}, reg, http, engine)
                mark = {"found": "✓", "link": "▲", "weak": "▲"}.get(r["result"], "✗")
                top = (r.get("top") or [{}])[0]
                _out(f"{mark} {r['result']:<8} {o.brand} {o.model}: {top.get('url', '')}")
    new = [o for o in offers if o.action == "new"]
    if args.report:
        Path(args.report).write_text(json.dumps([o.as_dict() for o in offers], indent=1), encoding="utf-8")
    if args.hacmd == "devices" or args.dry_run:
        _out("\n" + summary(offers, dry_run=args.hacmd == "import"))
        return 0
    if not new:
        _out("\n" + summary(offers) + ": nothing to add")
        return 0
    answer = _confirm(f"\nAdd {len(new)} new item(s) to Homebox at {cfg.homebox.url}?", args.yes)
    if answer == "n":
        _out("Nothing written.")
        return 0
    if answer == "e":
        new = [o for o in new if _confirm(f"  add {o.device.name} ({o.brand} {o.model})?", False) == "y"]
    made = 0
    assert hb is not None
    for o, _item_id, error in add_to_homebox(hb, new):
        if error:
            _out(f"✗ refused {o.device.name}: {error}")
        else:
            made += 1
            _out(f"✓ added   {o.device.name} ({o.brand} {o.model}) in {o.location or 'no location'}")
    hb.close()
    _out(f"{made} added to Homebox. `manualbeagle sync homebox` finds their manuals.")
    return 0


def _paperless_docs(args: argparse.Namespace, lib, ledger) -> list[dict]:
    """The library documents `paperless send` picks: matching args.match, and not sent yet unless --again."""
    docs = lib.documents()
    if args.match:
        m = args.match.lower()
        docs = [
            d
            for d in docs
            if d["sha256"].startswith(m)
            or m in " ".join(str(d.get(k) or "") for k in ("brand", "model", "title")).lower()
        ]
    return docs if args.again else ledger.unsent(docs)


def cmd_paperless(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle paperless status|send`: file the library's manuals in Paperless-ngx."""
    from .library import Library
    from .sinks.paperless import Ledger, PaperlessError, check_queued, from_config, send, title_for

    lib = Library(cfg.library, cfg.db_path)
    ledger = Ledger(lib.db)
    try:
        if args.pcmd == "status":
            pl = from_config(cfg)
            _out(f"✓ Paperless-ngx {pl.status()} at {cfg.paperless.url}")
            for f in check_queued(pl, ledger):
                _out(f"  {f.state}: {f.sha256[:12]} {f.message}")
            counts = ledger.counts()
            unsent = len(ledger.unsent(lib.documents()))
            _out(
                f"  ✓ {counts.get('filed', 0)} filed, = {counts.get('exists', 0)} there already, "
                f"… {counts.get('queued', 0)} queued, ✗ {counts.get('failed', 0)} failed, {unsent} not sent"
            )
            pl.close()
            return 0
        docs = _paperless_docs(args, lib, ledger)
        if not docs:
            _out("Nothing to send: every matching manual is in Paperless already (--again sends it anyway).")
            return 0
        for d in docs:
            title = title_for(d.get("brand") or "", d.get("model") or "", d.get("kind"))
            _out(f"→ {title}  ({d.get('pages') or '?'} pages, {d['sha256'][:12]})")
        tags = ", ".join(["brand", "model", *cfg.paperless.tags])
        if args.dry_run:
            _out(
                f"\nDRY RUN, nothing sent. {len(docs)} manual(s); tags: {tags}; "
                f"type: {cfg.paperless.document_type or 'none'}"
            )
            return 0
        answer = _confirm(f"\nSend {len(docs)} manual(s) to Paperless-ngx at {cfg.paperless.url}?", args.yes)
        if answer == "n":
            _out("Nothing sent.")
            return 0
        if answer == "e":
            docs = [d for d in docs if _confirm(f"  send {d.get('brand')} {d.get('model')}?", False) == "y"]
        pl = from_config(cfg)
        wait = args.wait if args.wait is not None else (120 if len(docs) == 1 else 0)
        glyph = {"filed": "✓ filed", "exists": "= there", "queued": "… queued", "failed": "✗ failed"}
        for f in send(pl, ledger, docs, cfg.paperless.document_type, cfg.paperless.tags, wait):
            _out(f"{glyph[f.state]:<9} {f.title}: {f.message}")
        pl.close()
    except PaperlessError as e:
        _out(f"✗ Paperless-ngx: {e}")
        return 1
    finally:
        lib.close()
    return 0


def paperless_auto(args: argparse.Namespace, cfg: Config) -> None:
    """With `[paperless] auto` on, file what a downloading command (find --fetch, fetch, batch --fetch, sync) added."""
    downloads = args.cmd in ("fetch", "sync") or (
        args.cmd in ("find", "batch") and getattr(args, "fetch", False)
    )
    if not (cfg.paperless.auto and downloads and cfg.paperless.url) or getattr(args, "dry_run", False):
        return
    from .library import Library
    from .sinks.paperless import PaperlessError, sweep

    lib = Library(cfg.library, cfg.db_path)
    try:
        sent = sweep(cfg, lib)
        if sent:
            ok = sum(1 for f in sent if f.state != "failed")
            _out(f"Paperless-ngx: {ok} of {len(sent)} new manual(s) sent ([paperless] auto is on)")
    except PaperlessError as e:
        _out(f"✗ Paperless-ngx: {e}")
    finally:
        lib.close()


def cmd_serve(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle serve --mcp | --web`: run the MCP server over stdio, or the web UI on localhost."""
    if args.mcp and args.web:
        _out("✗ choose one of --mcp and --web")
        return 2
    if args.mcp:
        from .mcp_server import run

        return run(cfg, allow_write=args.allow_write)
    if args.web:
        try:
            from .web.app import run as run_web
        except ImportError:
            _out('✗ the web UI needs its extra: pip install -e ".[web]"')
            return 2
        return run_web(
            cfg,
            host=args.host,
            port=args.port,
            token=args.token,
            open_browser=args.open,
            public_url=args.public_url or "",
            file_data=read_file(Path(args.config) if args.config else None),
        )
    _out("✗ choose --mcp (for an AI assistant) or --web (the web UI)")
    return 2


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for every subcommand."""
    p = argparse.ArgumentParser(
        prog="manualbeagle", description="Find the maker's manual for a brand and model."
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", help="config file (default: per-user config.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find", help="find manuals for a brand and model")
    f.add_argument("brand")
    f.add_argument("model")
    f.add_argument("--year", type=int, help="model year, for vehicles")
    f.add_argument("--kind", choices=[k.value for k in DocKind], help="prefer this kind of document")
    f.add_argument("--adapter", help="use only this adapter")
    f.add_argument(
        "--search", action="store_true", help="also run the web search when an adapter finds something"
    )
    f.add_argument("--no-search", action="store_true", help="never run the web search")
    f.add_argument("--searxng", help="SearXNG base URL")
    f.add_argument("--limit", type=int, default=8)
    f.add_argument("--why", action="store_true", help="show why each result scored as it did")
    f.add_argument("--fetch", action="store_true", help="download the best result and check its text")
    f.add_argument("--json", action="store_true")
    f.set_defaults(func=cmd_find)

    a = sub.add_parser("adapters", help="list or test brand adapters")
    asub = a.add_subparsers(dest="acmd", required=True)
    al = asub.add_parser("list")
    al.set_defaults(func=cmd_adapters_list)
    at = asub.add_parser("test", help="run adapter test cases (offline from fixtures by default)")
    at.add_argument("ids", nargs="*")
    at.add_argument("--live", action="store_true", help="against the real sites")
    at.add_argument("--record", action="store_true", help="against the real sites, saving fixtures")
    at.add_argument(
        "--check-text", action="store_true", help="live: download the hit and check text_contains"
    )
    at.add_argument("--fixtures", help="fixtures folder (default: the repo's tests/fixtures)")
    at.add_argument("--json", metavar="FILE", help="also write every case's result here, as JSON")
    at.add_argument("-v", "--verbose", action="store_true")
    at.set_defaults(func=cmd_adapters_test)

    sc = asub.add_parser("scoreboard", help="the brand table in docs/brands.md")
    sc.add_argument("--write", help="write it to this file")
    sc.add_argument(
        "--live", metavar="FILE", help="add a Live column from `adapters test --live --json FILE`"
    )
    sc.add_argument("--live-when", help="when that live run was, for the header (default: today)")
    sc.set_defaults(func=cmd_scoreboard)

    rp = asub.add_parser("report", help="what broke in a live run, as Markdown (exit 1 if anything did)")
    rp.add_argument("results", help="the JSON file from `adapters test --live --json FILE`")
    rp.add_argument("--write", help="write it to this file")
    rp.add_argument("--run-url", help="a link to the run, for the report's first line")
    rp.set_defaults(func=cmd_adapters_report)

    ba = sub.add_parser("batch", help="find the best manual for every row of a YAML or CSV list")
    ba.add_argument("file")
    ba.add_argument("--min-score", type=float, default=60)
    ba.add_argument("--report", help="write a JSON report here")
    ba.add_argument(
        "--fetch",
        action="store_true",
        help="download each manual into the library (as sync does) and print coverage by brand",
    )
    ba.add_argument(
        "--export",
        metavar="DIR",
        help="with --fetch: copy the chosen manuals to DIR as Brand/Model - Kind.pdf, with an index.md of every row",
    )
    ba.set_defaults(func=cmd_batch)

    ex = sub.add_parser("export", help="copy the library to a folder: Brand/Model - Kind.pdf")
    ex.add_argument("dest")
    ex.set_defaults(func=cmd_export)

    fe = sub.add_parser("fetch", help="download one URL into the library and index it")
    fe.add_argument("url")
    fe.add_argument("--brand", required=True)
    fe.add_argument("--model", required=True)
    fe.add_argument("--title")
    fe.set_defaults(func=cmd_fetch)

    s = sub.add_parser("search", help="full-text search of the library, per page")
    s.add_argument("query")
    s.add_argument("--brand")
    s.add_argument("--model")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_search)

    ak = sub.add_parser("ask", help="answer a question from the library's manuals, citing the pages")
    ak.add_argument("question")
    ak.add_argument("--brand", help="search only this brand's manuals")
    ak.add_argument("--model", help="search only this model's manuals")
    ak.add_argument(
        "--document", help="search only this document (its sha256, or the start of it as `docs` prints it)"
    )
    ak.add_argument("--pages", type=int, default=4, help="how many pages the model reads (1 to 8; default 4)")
    ak.add_argument("--no-model", action="store_true", help="don't ask the [ai] model: print the best pages")
    ak.add_argument("--json", action="store_true", help="print the answer and its pages as JSON")
    ak.set_defaults(func=cmd_ask)

    d = sub.add_parser("docs", help="list documents in the library")
    d.set_defaults(func=cmd_docs)

    oc = sub.add_parser("ocr", help="OCR library documents that have no text (after installing OCRmyPDF)")
    oc.set_defaults(func=cmd_ocr)

    sy = sub.add_parser("sync", help="find manuals for an inventory's items and attach them")
    sy.add_argument("source", choices=["homebox"])
    sy.add_argument("--dry-run", action="store_true", help="show what it would attach; change nothing")
    sy.add_argument("--item", help="only items whose name contains this")
    sy.add_argument("--links-only", action="store_true", help="attach the maker's URL, never upload a PDF")
    sy.add_argument("--report", help="write a JSON report here")
    sy.add_argument(
        "--metrics",
        metavar="FILE",
        help="also write the outcome here as a Prometheus textfile (node_exporter)",
    )
    sy.add_argument("--min-score", type=float, default=60)
    sy.add_argument("--force", action="store_true", help="also items that already have a manual")
    sy.add_argument(
        "--allow-search-links",
        action="store_true",
        help="attach links found by web search, not only the maker's",
    )
    sy.set_defaults(func=cmd_sync)

    hb = sub.add_parser("homebox", help="look at or seed a Homebox")
    hsub = hb.add_subparsers(dest="hcmd", required=True)
    hsub.add_parser("status").set_defaults(func=cmd_homebox)
    hsub.add_parser("items", help="list items; ✓ marks one with a manual").set_defaults(func=cmd_homebox)
    hs = hsub.add_parser("seed", help="add items from a YAML list (name, brand, model, location)")
    hs.add_argument("file")
    hs.add_argument("--dry-run", action="store_true")
    hs.set_defaults(func=cmd_homebox)

    ha = sub.add_parser("ha", help="read Home Assistant's devices: find manuals, offer them as Homebox items")
    hasub = ha.add_subparsers(dest="hacmd", required=True)
    hasub.add_parser("status", help="sign in to Home Assistant and print its version").set_defaults(
        func=cmd_ha
    )
    hd = hasub.add_parser("devices", help="list devices and what an import would do with each")
    hd.add_argument("--all", action="store_true", help="also list the devices an import skips")
    hi = hasub.add_parser("import", help="offer devices Homebox doesn't have as new items (asks first)")
    hi.add_argument("--dry-run", action="store_true", help="show what it would add; change nothing")
    hi.add_argument("--yes", action="store_true", help="add every new item without asking (after a dry run)")
    hi.add_argument(
        "--find", action="store_true", help="also look up each device's manual (downloads nothing)"
    )
    for x in (hd, hi):
        x.add_argument("--only", help="only devices whose name contains this")
        x.add_argument("--report", help="write a JSON report here")
        x.set_defaults(func=cmd_ha)

    pa = sub.add_parser("paperless", help="file the library's manuals in Paperless-ngx")
    pasub = pa.add_subparsers(dest="pcmd", required=True)
    pasub.add_parser("status", help="check the token; count what was sent").set_defaults(func=cmd_paperless)
    ps = pasub.add_parser("send", help="send manuals Paperless doesn't have (asks first)")
    ps.add_argument(
        "match", nargs="?", help="only manuals whose brand, model or title contains this, or a sha256"
    )
    ps.add_argument("--dry-run", action="store_true", help="list what it would send; send nothing")
    ps.add_argument("--yes", action="store_true", help="send without asking")
    ps.add_argument(
        "--again", action="store_true", help="also manuals sent before (Paperless's copy is still checked)"
    )
    ps.add_argument(
        "--wait", type=float, help="seconds to wait for Paperless to consume each (default 120 for one)"
    )
    ps.set_defaults(func=cmd_paperless)

    mn = sub.add_parser("maintenance", help="maintenance tasks read from manuals, for Homebox")
    msub = mn.add_subparsers(dest="mcmd", required=True)
    ms = msub.add_parser(
        "suggest", help="read the library's manuals for tasks with an interval; keep new ones"
    )
    ms.add_argument(
        "--ai", action="store_true", help="also ask the [ai] model, which adds to the rules' findings"
    )
    ms.add_argument("--no-homebox", action="store_true", help="don't match documents to Homebox items")
    ms.add_argument("--document", metavar="SHA", help="only this document (its sha256, or the start of it)")
    ms.set_defaults(func=cmd_maintenance)
    ml = msub.add_parser("list", help="list suggestions (approve them on the web UI's Maintenance page)")
    ml.add_argument("--status", choices=["new", "filed", "dismissed", "all"], default="new")
    ml.add_argument("--json", action="store_true", help="print them as JSON")
    ml.set_defaults(func=cmd_maintenance)

    np = sub.add_parser("nameplate", help="read a rating plate photo: brand, model and serial (Tesseract)")
    np.add_argument("photo", help="a JPEG or PNG of the rating plate, upright")
    np.add_argument("--json", action="store_true", help="print the reading as JSON")
    np.add_argument("--text", action="store_true", help="also print the text OCR read")
    np.set_defaults(func=cmd_nameplate)

    sv = sub.add_parser("serve", help="run the MCP server, or the web UI")
    sv.add_argument("--mcp", action="store_true", help="MCP over stdio")
    sv.add_argument("--allow-write", action="store_true", help="MCP: turn on tools that save files")
    sv.add_argument("--web", action="store_true", help="the web UI and its HTTP API, on this computer")
    sv.add_argument("--host", default="127.0.0.1", help="web: address to listen on (default 127.0.0.1)")
    sv.add_argument("--port", type=int, default=8765, help="web: port (default 8765)")
    sv.add_argument(
        "--token",
        action="store_true",
        help="web: require a secret link even on 127.0.0.1 (always required on any other address)",
    )
    sv.add_argument("--open", action="store_true", help="web: open the UI in the default browser")
    sv.add_argument(
        "--public-url",
        metavar="URL",
        help="web: the https:// address of a reverse proxy in front of it; accepts that name and needs a token",
    )
    sv.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point of the `manualbeagle` command. Returns the process exit code."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    cfg = load(Path(args.config) if args.config else None)
    try:
        code = args.func(args, cfg)
        paperless_auto(args, cfg)
        return code
    except KeyboardInterrupt:
        return 130
    except json.JSONDecodeError as e:
        _out(f"✗ a site answered with something that isn't JSON: {e}")
        return 1
