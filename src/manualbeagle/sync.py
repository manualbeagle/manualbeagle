"""`manualbeagle sync homebox`: find a manual for every item that has a maker and model but no manual, and attach it.

For each item: run the pipeline, then try the best candidates in order. A PDF that downloads and whose text names
the model is uploaded; one that downloads but doesn't name the model, or a link-only result, is attached as a link
to the maker's URL instead. --dry-run does all the finding and checking (it fills the local library) but writes
nothing to Homebox.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .adapters import Registry
from .config import Config
from .http import FetchError
from .library import Library
from .models import KIND_LABELS, Candidate, Query
from .pipeline import find
from .search import make_engine
from .sinks.homebox import Homebox, HomeboxError, Item
from .verify import host_matches


@dataclass
class Plan:
    """What sync decided for one item, and whether it was written to Homebox."""

    item: str
    item_id: str
    brand: str
    model: str
    action: str  # upload | link | skip | none
    reason: str = ""
    url: str = ""
    title: str = ""
    kind: str = ""
    score: float = 0
    pdf_check: list[str] = field(default_factory=list)
    file: str = ""
    done: bool = False


def _out(s: str) -> None:
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def attachment_name(brand: str, model: str, c: Candidate) -> str:
    """The file name a manual gets in Homebox: 'Brand Model - Kind.pdf', with unsafe characters replaced."""
    kind = KIND_LABELS.get(c.kind, "Manual") if c.kind else "Manual"
    safe = "".join(ch if ch.isalnum() or ch in " -._" else "_" for ch in f"{brand} {model} - {kind}")
    return safe.strip() + ".pdf"


def plan_item(item: Item, cfg: Config, reg: Registry, http, lib: Library, args: argparse.Namespace) -> Plan:
    """Decide what to attach to one item, downloading candidates into the library to check them.

    The first of the top three candidates (scoring at least `args.min_score`) whose downloaded PDF names the
    model is an upload; else a manual an earlier run put in the library for the same brand and model, whose text
    names it (web search answers differently from run to run, and a manual once found shouldn't be lost); else
    the best maker's link (or any link with `args.allow_search_links`); else nothing. Items without a maker and
    model, or that already have a manual (unless `args.force`), are skipped.
    """
    p = _plan_from_search(item, cfg, reg, http, lib, args)
    if p.action in ("link", "none") and not getattr(args, "links_only", False):
        earlier_manual(lib, p)
    return p


def earlier_manual(lib: Library, p: Plan) -> bool:
    """Turn `p` into an upload of a library document already linked to its brand and model, if one names the model.

    Of several, an owner's manual comes first, then the one checked best. Returns whether it found one.
    """
    from .normalize import normalise
    from .verify import check_pdf_text

    q = normalise(Query(brand=p.brand, model=p.model))
    best = None
    for d in lib.linked(q.canonical_brand or q.brand, p.model):
        score, _ = check_pdf_text(lib.page_texts(d["sha256"]), q)
        if score > 0 and Path(d["path"]).exists():
            rank = (d.get("kind") == "owners_manual", score)
            if best is None or rank > best[0]:
                best = (rank, d)
    if not best:
        return False
    d = best[1]
    p.action, p.url, p.title, p.kind, p.file = (
        "upload",
        d["source_url"],
        d["title"] or "",
        d["kind"] or "",
        d["path"],
    )
    p.reason = "kept from an earlier run: a manual in the library whose text names the model"
    return True


def _plan_from_search(
    item: Item, cfg: Config, reg: Registry, http, lib: Library, args: argparse.Namespace
) -> Plan:
    """`plan_item` without the library fallback: what this run's search and downloads found."""
    p = Plan(item=item.name, item_id=item.id, brand=item.manufacturer, model=item.model, action="none")
    if not item.manufacturer or not item.model:
        p.action, p.reason = "skip", "no manufacturer or model number"
        return p
    if item.manuals and not getattr(args, "force", False):
        p.action, p.reason = "skip", f"already has {len(item.manuals)} manual(s)"
        return p
    engine = make_engine(cfg.search.engine, url=cfg.search.url)
    res = find(Query(brand=item.manufacturer, model=item.model), reg, http, engine)
    q = res.query
    good = [c for c in res.candidates if c.score >= args.min_score]
    if not good:
        best = res.best
        p.reason = f"nothing scored {args.min_score:.0f}+" + (
            f" (best {best.score:.0f}: {best.url})" if best else ""
        )
        return p
    for c in good[:3]:
        if c.link_only or args.links_only:
            continue
        try:
            doc = lib.fetch(c, q, http, item=item.id)
        except FetchError as e:
            p.pdf_check.append(f"{c.url}: {e}")
            continue
        p.pdf_check += doc.check_reasons or []
        trusted = trusted_by_adapter(c, doc.check_reasons or [], reg)
        if doc.check_score > 0 or trusted:
            p.action, p.url, p.title, p.score = "upload", c.url, c.title, c.score
            p.kind = c.kind.value if c.kind else ""
            p.file = str(doc.path)
            p.reason = trusted if doc.check_score <= 0 else "PDF text names the model"
            return p
    # No PDF passed the text check: attach the best result as a link, but only the maker's own (an adapter's
    # result, or a URL on one of its domains), unless the user allows links from web search.
    domains = [d for a in reg.for_brand(item.manufacturer) for d in a.spec.domains]
    for c in good:
        url = c.page_url or c.url
        if urlparse(url).scheme not in ("http", "https"):
            continue
        maker = not c.source.startswith("search:") or host_matches(urlparse(url).hostname or "", domains)
        if not maker and not getattr(args, "allow_search_links", False):
            continue
        p.action, p.url, p.title, p.score = "link", url, plain(c.title), c.score
        p.kind = c.kind.value if c.kind else ""
        p.reason = (
            "link only" if c.link_only else "no downloaded PDF named the model; linking the maker's page"
        )
        return p
    p.reason = "only web-search links, not the maker's (--allow-search-links to attach them)"
    return p


def trusted_by_adapter(c: Candidate, reasons: list[str], reg: Registry) -> str:
    """Why an adapter's own result may be uploaded though its PDF text doesn't name the model, or "".

    Only for a result an adapter found on the maker's site for this model: a PDF that names a related model (a
    longer SKU, or the base tool of a kit), or any PDF with text from an adapter whose maker publishes family
    manuals. A web-search result never qualifies.
    """
    if not c.source.startswith("adapter:") or any(r.startswith("no text extracted") for r in reasons):
        return ""
    adapter = reg.adapters.get(c.source.split(":", 1)[1])
    if adapter is None:
        return ""
    related = next((r for r in reasons if r.startswith("PDF text names a related model")), "")
    if related:
        return (
            "the maker's document for this model; its text names a related model ("
            + related.split("(", 1)[1].split(")")[0]
            + ")"
        )
    if adapter.spec.family_manuals:
        return "the maker's family manual for this model (it doesn't name each model)"
    return ""


def plain(s: str, limit: int = 120) -> str:
    """A title as plain text: no markup or control characters, and not too long."""
    s = re.sub(r"<[^>]*>|[\x00-\x1f\x7f]", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()[:limit]


def attach(hb: Homebox, p: Plan) -> None:
    """Write one plan to Homebox: upload its PDF, or attach its link. Sets `p.done`.

    Plans whose action is skip or none are left alone. Raises HomeboxError when Homebox refuses.
    """
    if p.action == "upload":
        c = Candidate(url=p.url, title=p.title, kind=p.kind or None)
        hb.attach_file(p.item_id, Path(p.file), attachment_name(p.brand, p.model, c))
    elif p.action == "link":
        hb.attach_link(p.item_id, p.url, p.title or plain(f"{p.brand} {p.model} manual"))
    else:
        return
    p.done = True


def summary(plans: list[Plan], dry_run: bool = False) -> str:
    """The count of each action, as one line with glyphs and words."""
    n = {a: sum(1 for p in plans if p.action == a) for a in ("upload", "link", "skip", "none")}
    return (
        f"{'DRY RUN, nothing written. ' if dry_run else ''}"
        f"✓ {n['upload']} upload, ▲ {n['link']} link, – {n['skip']} skipped, ✗ {n['none']} not found"
    )


def homebox(cfg: Config) -> Homebox:
    """A Homebox client from the config's `[homebox]` table. Raises HomeboxError when it isn't set up.

    Also when the address was changed on the Settings page alone: the key goes only to the address set beside it.
    """
    held = cfg.homebox.key_held_back(cfg.homebox.url)
    if held:
        raise HomeboxError(f"not connecting: {held}")
    return Homebox(cfg.homebox.url, cfg.homebox.token())


def write_metrics(
    path: str | Path, plans: list[Plan], ok: bool, refused: int, seconds: float, dry_run: bool
) -> None:
    """Write a run's outcome as a Prometheus textfile (for node_exporter's textfile collector), atomically.

    `manualbeagle_sync_success` is 1 when Homebox was read and accepted every write tried. The file is written
    to a temporary name beside it and renamed, so the collector never reads half of it.
    """
    import os
    import time

    n = {a: sum(1 for p in plans if p.action == a) for a in ("upload", "link", "skip", "none")}
    lines = [
        "# HELP manualbeagle_sync_last_run_timestamp_seconds When manualbeagle sync homebox last finished.",
        "# TYPE manualbeagle_sync_last_run_timestamp_seconds gauge",
        f"manualbeagle_sync_last_run_timestamp_seconds {time.time():.0f}",
        "# HELP manualbeagle_sync_success 1 if Homebox was read and accepted every write tried, else 0.",
        "# TYPE manualbeagle_sync_success gauge",
        f"manualbeagle_sync_success {int(ok)}",
        "# HELP manualbeagle_sync_duration_seconds How long the run took.",
        "# TYPE manualbeagle_sync_duration_seconds gauge",
        f"manualbeagle_sync_duration_seconds {seconds:.1f}",
        "# HELP manualbeagle_sync_items Items by what the run decided: upload, link, skip (no model, or a manual"
        " already) or none (nothing found).",
        "# TYPE manualbeagle_sync_items gauge",
        *(f'manualbeagle_sync_items{{action="{a}"}} {v}' for a, v in n.items()),
        "# HELP manualbeagle_sync_attached Manuals written to Homebox (PDFs uploaded and links attached).",
        "# TYPE manualbeagle_sync_attached gauge",
        f"manualbeagle_sync_attached {sum(1 for p in plans if p.done)}",
        "# HELP manualbeagle_sync_refused Writes Homebox refused.",
        "# TYPE manualbeagle_sync_refused gauge",
        f"manualbeagle_sync_refused {refused}",
        "# HELP manualbeagle_sync_dry_run 1 if the run was a dry run, which writes nothing.",
        "# TYPE manualbeagle_sync_dry_run gauge",
        f"manualbeagle_sync_dry_run {int(dry_run)}",
    ]
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def sync(args: argparse.Namespace, cfg: Config) -> int:
    """`manualbeagle sync homebox`: plan every item, attach unless --dry-run, and print a summary.

    With `--metrics FILE`, the outcome is also written there as a Prometheus textfile, whether or not the run
    got as far as reading Homebox. Returns 1 when Homebox couldn't be read or refused a write, else 0.
    """
    import time

    started = time.monotonic()
    plans: list[Plan] = []
    refused = 0
    ok = False
    try:
        try:
            hb = homebox(cfg)
            items = hb.items()
        except (HomeboxError, OSError, httpx.HTTPError) as e:
            _out(f"✗ Homebox: {e}")
            return 1
        if args.item:
            items = [i for i in items if args.item.lower() in i.name.lower()]
        reg = Registry(cfg.adapter_dirs)
        lib = Library(cfg.library, cfg.db_path)
        with cfg.client() as http:
            for item in items:
                p = plan_item(item, cfg, reg, http, lib, args)
                plans.append(p)
                glyph = {"upload": "✓ upload", "link": "▲ link", "skip": "– skip", "none": "✗ none"}[p.action]
                _out(f"{glyph:<10} {p.item[:34]:<34} {p.brand} {p.model}: {p.reason}")
                if p.url:
                    _out(f"           {p.url}")
                if args.dry_run or p.action not in ("upload", "link"):
                    continue
                if p.action == "link" and not cfg.homebox.attach_links:
                    _out("           – not attached: [homebox] attach_links is off")
                    continue
                try:
                    attach(hb, p)
                except HomeboxError as e:
                    refused += 1
                    _out(f"           ✗ Homebox refused: {e}")
        lib.close()
        hb.close()
        _out("\n" + summary(plans, args.dry_run))
        if args.report:
            Path(args.report).write_text(json.dumps([asdict(p) for p in plans], indent=1), encoding="utf-8")
        ok = refused == 0
        return 0 if ok else 1
    finally:
        if getattr(args, "metrics", None):
            write_metrics(args.metrics, plans, ok, refused, time.monotonic() - started, args.dry_run)
