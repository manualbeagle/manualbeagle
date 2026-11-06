"""`manualbeagle batch FILE`: find the best manual for every row of a list, for people with no inventory app.

FILE is YAML (a list of {name, brand, model}) or CSV with brand and model columns (name optional). Prints one line
per row with a glyph and a word, and can write a JSON report.

With --fetch it also downloads: for each row it makes the same decision as `sync homebox` (download the best PDF
whose own text names the model, else keep the maker's link), stores the PDF in the library (searchable per page,
and exportable with `manualbeagle export`), and ends with coverage by brand.

The web UI runs the same rows through `look_up` (without --fetch) and `row_item` plus `sync.plan_item` (with it).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import yaml

from .adapters import Registry
from .config import Config
from .models import KIND_LABELS, Query
from .pipeline import find
from .search import make_engine
from .sinks.homebox import Item

# Each result of `look_up` as a glyph and a word.
GLYPHS = {
    "found": "✓ found",
    "link": "▲ link",
    "weak": "▲ weak",
    "none": "✗ none",
    "nosearch": "! search",
    "skip": "– skip",
}


def read_rows(path: Path) -> list[dict]:
    """Read a YAML list of mappings, or a CSV file with lower-cased column names and stripped values."""
    return parse_rows(path.read_text(encoding="utf-8"), is_csv=path.suffix.lower() == ".csv")


def parse_rows(text: str, is_csv: bool) -> list[dict]:
    """Parse a list from text: CSV with a header row (lower-cased column names, stripped values), or YAML.

    Raises ValueError when the YAML isn't a list of mappings.
    """
    if is_csv:
        return [
            {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            for r in csv.DictReader(text.splitlines())
        ]
    rows = yaml.safe_load(text) or []
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("the YAML must be a list of mappings, each with a brand and a model")
    return rows


def look_up(row: dict, reg: Registry, http, engine, min_score: float = 60) -> dict:
    """Find the best manual for one row and say how good it is, downloading nothing.

    Returns the row's `name`, `brand`, `model` and `result`: found, link, weak, none, nosearch (the web search
    failed, so "none" can't be trusted) or skip (no brand or model). A row that was looked up also has its
    `adapters` reports, `searched`, `search_error` and the `top` three candidates.
    """
    brand, model = str(row.get("brand") or ""), str(row.get("model") or "")
    name = str(row.get("name") or f"{brand} {model}")
    if not brand or not model:
        return {"name": name, "brand": brand, "model": model, "result": "skip"}
    res = find(Query(brand=brand, model=model), reg, http, engine)
    best = res.best
    if best is None:
        state = "none"
    elif best.score < min_score:
        state = "weak"
    elif best.link_only:
        state = "link"
    else:
        state = "found"
    if state in ("none", "weak") and res.search_error:
        state = "nosearch"  # not "nothing exists": the web search couldn't run
    return {
        "name": name,
        "brand": brand,
        "model": model,
        "result": state,
        "adapters": [r.model_dump() for r in res.adapters],
        "searched": res.searched,
        "search_error": res.search_error,
        "top": [c.model_dump(mode="json") for c in res.candidates[:3]],
    }


def row_item(n: int, row: dict) -> Item:
    """Row `n` (0-based) of a list as an inventory Item, so `sync.plan_item` can decide what to download."""
    brand, model = str(row.get("brand") or ""), str(row.get("model") or "")
    name = str(row.get("name") or f"{brand} {model}")
    return Item(id=f"row{n + 1}", name=name, manufacturer=brand, model=model)


def harvest_args(min_score: float = 60) -> argparse.Namespace:
    """The options `sync.plan_item` gets for a list's rows: sync's defaults, without its Homebox-only switches."""
    return argparse.Namespace(min_score=min_score, links_only=False, force=False, allow_search_links=False)


def run(
    path: Path,
    cfg: Config,
    min_score: float = 60,
    report: str | None = None,
    fetch: bool = False,
    export: str | None = None,
) -> int:
    """Find the best manual for every row of `path` and print one line per row with a glyph and a word.

    Rows without a brand or model are skipped. `min_score` separates a found manual from a weak one; `report`
    names a JSON file to write. With `fetch`, hands over to `harvest`, which also downloads. Returns the exit
    code.
    """
    if fetch:
        return harvest(path, cfg, min_score, report, export)
    reg = Registry(cfg.adapter_dirs)
    engine = make_engine(cfg.search.engine, url=cfg.search.url)
    out = []
    with cfg.client() as http:
        for row in read_rows(path):
            r = look_up(row, reg, http, engine, min_score)
            out.append(r)
            if r["result"] == "skip":
                sys.stdout.write(f"– skip   {r['name']}: no model number\n")
                continue
            best = r["top"][0] if r["top"] else None
            kind = KIND_LABELS.get(best["kind"], "?") if best and best["kind"] else "?"
            sys.stdout.write(f"{GLYPHS[r['result']]:<8} {r['name'][:40]:<40} {r['brand']} {r['model']}")
            sys.stdout.write(f"  → {best['score']:.0f} {kind}: {best['url']}\n" if best else "\n")
            if r["result"] == "nosearch":
                sys.stdout.write(f"         web search failed: {r['search_error']}\n")
            sys.stdout.flush()
    sys.stdout.write("\n" + summary(out) + "\n")
    if report:
        Path(report).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


def summary(rows: list[dict]) -> str:
    """The count of each result of `look_up`, as one line with glyphs and words."""
    n = {k: sum(r["result"] == k for r in rows) for k in GLYPHS}
    return (
        f"✓ {n['found']} found, ▲ {n['link']} link only, ▲ {n['weak']} weak, ✗ {n['none']} none, "
        f"! {n['nosearch']} search failed, – {n['skip']} no model"
    )


def harvest(
    path: Path, cfg: Config, min_score: float = 60, report: str | None = None, export: str | None = None
) -> int:
    """Download a manual for every row (`batch --fetch`) and print coverage by brand.

    With `export`, copy only the chosen manuals to that folder (Brand/Model - Kind.pdf) and write its
    index.md: every row with its manual, the maker's link, or why there's neither.
    """
    from .library import Library
    from .sync import plan_item

    args = harvest_args(min_score)
    reg = Registry(cfg.adapter_dirs)
    lib = Library(cfg.library, cfg.db_path)
    plans = []
    glyphs = {"upload": "✓ manual", "link": "▲ link", "skip": "– skip", "none": "✗ none"}
    with cfg.client() as http:
        for n, row in enumerate(read_rows(path)):
            p = plan_item(row_item(n, row), cfg, reg, http, lib, args)
            plans.append(p)
            sys.stdout.write(f"{glyphs[p.action]:<9} {p.item[:40]:<40} {p.brand} {p.model}: {p.reason}\n")
            if p.url:
                sys.stdout.write(f"          {p.url}\n")
            sys.stdout.flush()
    lib.close()
    sys.stdout.write("\n" + coverage(plans))
    if export:
        from .sinks.folder import export_plans

        index = export_plans(plans, Path(export), source=path.name)
        sys.stdout.write(f"\n✓ {sum(p.action == 'upload' for p in plans)} manuals and the index in {index}\n")
    if report:
        from dataclasses import asdict

        Path(report).write_text(json.dumps([asdict(p) for p in plans], indent=1), encoding="utf-8")
    return 0


def coverage(plans: list) -> str:
    """Items with a downloaded manual out of all items, overall and by brand, with each row's glyph and word."""
    by: dict[str, list] = {}
    for p in plans:
        by.setdefault(p.brand or "(no brand)", []).append(p)

    def line(label: str, ps: list) -> str:
        got = sum(p.action == "upload" for p in ps)
        link = sum(p.action == "link" for p in ps)
        return (
            f"{label:<26} ✓ {got} of {len(ps)} with a manual"
            + (f", ▲ {link} maker's link" if link else "")
            + "\n"
        )

    out = line("All items", plans)
    for brand in sorted(by, key=lambda b: (-sum(p.action == "upload" for p in by[b]), b.lower())):
        out += line("  " + brand[:24], by[brand])
    return out
