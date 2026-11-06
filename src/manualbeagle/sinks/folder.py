"""The plain-folder sink: Brand/Model - Kind.pdf, with a small JSON file beside each PDF. The zero-setup default."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ..library import Library
from ..models import KIND_LABELS, DocKind


def safe(s: str) -> str:
    """A string made safe as a file or folder name on Windows, macOS and Linux."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", s).strip(" .") or "_"


def export(lib: Library, dest: Path) -> list[Path]:
    """Copy every document in the library to `dest` as Brand/Model - Kind.pdf, with a JSON file of metadata.

    Files already there with the same content are left alone; a different file with the same name gets a
    number. Returns the paths written or found.
    """
    out = []
    for d in lib.documents():
        kind = KIND_LABELS.get(DocKind(d["kind"]), "Document") if d.get("kind") else "Document"
        folder = dest / safe(d["brand"] or "Unknown")
        name = f"{safe(d['model'] or d['sha256'][:10])} - {kind}"
        target = folder / f"{name}.pdf"
        n = 2
        while target.exists() and _sha(target.with_suffix(".json")) not in ("", d["sha256"]):
            target = folder / f"{name} ({n}).pdf"
            n += 1
        folder.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(d["path"], target)
        meta = {k: d[k] for k in ("sha256", "title", "kind", "brand", "model", "pages", "source_url")}
        target.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        out.append(target)
    return out


def _sha(meta: Path) -> str:
    try:
        return json.loads(meta.read_text(encoding="utf-8")).get("sha256", "")
    except (OSError, ValueError):
        return ""


def export_plans(plans: list, dest: Path, source: str = "") -> Path:
    """Copy each planned upload's PDF to `dest` as Brand/Model - Kind.pdf and write `dest`/index.md.

    `plans` are `sync.Plan`s from `batch --fetch`. The index lists every row with a glyph and a word: ✓ manual (the
    file, and where it came from), ▲ link (the maker's page), – skipped or ✗ none (with the reason). Returns the
    index's path.
    """
    dest.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Manuals",
        "",
        f"Found by Manual Beagle{' from ' + source if source else ''}. ✓ manual = a PDF whose text names the model (or the "
        "maker's own document for it), ▲ link = the maker's page to open, ✗ none or – skipped = the reason.",
        "",
    ]
    for p in sorted(plans, key=lambda p: ((p.brand or "").lower(), (p.model or "").lower(), p.item)):
        what = f"**{p.item}**, {p.brand} {p.model}".rstrip()
        if p.action == "upload" and p.file:
            kind = KIND_LABELS.get(DocKind(p.kind), "Document") if p.kind else "Document"
            folder = dest / safe(p.brand or "Unknown")
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{safe(p.model)} - {kind}.pdf"
            shutil.copyfile(p.file, target)
            rel = target.relative_to(dest).as_posix()
            lines.append(f"- ✓ manual: {what}: [{rel}](<{rel}>) from <{p.url}>")
        elif p.action == "link":
            lines.append(f"- ▲ link: {what}: <{p.url}>")
        elif p.action == "skip":
            lines.append(f"- – skipped: {what}: {p.reason}")
        else:
            lines.append(f"- ✗ none: {what}: {p.reason}")
    index = dest / "index.md"
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return index
