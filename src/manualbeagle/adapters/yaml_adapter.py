"""Declarative adapters: search steps described in YAML."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote, quote_plus, urljoin

from jsonpath_ng.ext import parse as jsonpath
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from ..models import Candidate, DocKind, Query
from .base import Adapter, Context, SearchStep


def template_vars(q: Query, variant: str) -> dict[str, str]:
    """The values a template can use for one variant: {model}, {model_q}, {model_slug}, {brand}..."""
    return {
        "model": variant,
        "model_lower": variant.lower(),
        "model_q": quote_plus(variant),
        "model_path": quote(variant, safe=""),
        "model_compact": re.sub(r"[^A-Za-z0-9]", "", variant),
        "model_slug": re.sub(r"[^a-z0-9]+", "-", variant.lower()).strip("-"),
        # For keep_title / keep_url: the variant as a regex, its words joined by any spaces, underscores or hyphens.
        "model_re": r"[\s_-]*".join(re.escape(w) for w in re.split(r"[\s_-]+", variant.strip()) if w),
        "brand": q.canonical_brand or q.brand,
        "year": str(q.year or ""),
        "year_or_blank": str(q.year) if q.year else "",
    }


def fill(obj: Any, vars: dict[str, str]) -> Any:
    """Fill templates in a string, or in every string of a dict or list; one that can't be filled stays."""
    if isinstance(obj, str):
        try:
            return obj.format(**vars)
        except (KeyError, IndexError, ValueError):
            return obj
    if isinstance(obj, dict):
        return {k: fill(v, vars) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill(v, vars) for v in obj]
    return obj


def _json_get(item: Any, path: str | None) -> Any:
    if not path:
        return None
    if not path.startswith("$"):
        return path  # a literal
    found = [m.value for m in jsonpath(path).find(item)]
    if not found:
        return None
    return found if len(found) > 1 else found[0]


def _html_get(node: Node, spec: str | None) -> str | None:
    """'a.title@href' -> attribute; 'h3' -> text; '@href' -> the result's own attribute; '.' -> its text."""
    if not spec:
        return None
    if spec.startswith("="):
        return spec[1:]  # a literal
    sel, _, attr = spec.partition("@")
    target = node if sel in ("", ".") else node.css_first(sel)
    if target is None:
        return None
    if attr:
        return target.attributes.get(attr)
    return target.text(separator=" ", strip=True)


class YamlAdapter(Adapter):
    """An adapter whose searching is described entirely by the `search` steps in its YAML."""

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep only what the steps read: regex matches, or the HTML of matched result nodes. JSON stays whole."""
        kept: list[str] = []
        for step in self.spec.search:
            if step.format == "regex":
                kept += [m.group(0) for m in re.finditer(step.results, body, re.S | re.I)]
            elif step.format == "html":
                kept += [n.html or "" for n in HTMLParser(body).css(step.results)]
            else:
                return body
        return "\n".join(kept) if kept else body[:2000]

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Run each search step with each model variant, most specific first, and collect the candidates."""
        out: list[Candidate] = []
        for step in self.spec.search:
            for variant in self.variants(q):
                found = list(self.run_step(step, q, variant, ctx))
                out.extend(found)
                if found and step.for_each_variant:
                    break
        return out

    def run_step(self, step: SearchStep, q: Query, variant: str, ctx: Context) -> Iterable[Candidate]:
        """Run one search step for one model variant: request, extract, filter, and de-duplicate by URL."""
        vars = template_vars(q, variant)
        url = fill(step.url, vars)
        resp = ctx.http.request(
            step.method,
            url,
            params=fill(step.params, vars),
            json_body=fill(step.json_body, vars),
            data=fill(step.form, vars),
            headers=fill(step.headers, vars),
        )
        ctx.note(f"{self.id}: {step.method} {resp.url} -> {resp.status}")
        if not resp.ok:
            return []
        base = step.base_url or resp.url
        rows = list(self.extract(step, resp.text))
        # One file listed under several labels (GE lists a combined manual as both "Installation
        # Instructions" and "Use and Care Manual"): keep it once, as an owner's manual if any label says so.
        found: dict[str, Candidate] = {}
        for row in rows:
            link = row.get("url")
            if isinstance(link, list):
                link = link[0] if link else None
            if not link:
                continue
            link = str(link).strip()
            if step.url_template:
                link = fill(step.url_template, {**vars, "url": link})
            link = urljoin(base, link)
            if step.keep_url and not re.search(fill(step.keep_url, vars), link, re.I):
                continue
            if step.drop_url and re.search(step.drop_url, link, re.I):
                continue
            title = _s(row.get("title"))
            if step.keep_title and not re.search(fill(step.keep_title, vars), title, re.I):
                continue
            if step.drop_title and re.search(step.drop_title, title, re.I):
                continue
            models = row.get("models")
            if isinstance(models, str):
                models = [models]
            page = row.get("page_url")
            c = self.candidate(
                url=link,
                title=title,
                kind=self.kind_for(_s(row.get("kind")) or None, title, link),
                language=_s(row.get("language")) or None,
                models=[str(m) for m in (models or [])],
                page_url=urljoin(base, page) if page else None,
                published=_s(row.get("published")) or None,
            )
            if link not in found or (
                c.kind == DocKind.owners_manual and found[link].kind != DocKind.owners_manual
            ):
                found[link] = c
        return list(found.values())

    def extract(self, step: SearchStep, body: str) -> Iterable[dict[str, Any]]:
        """The raw fields of every result in a response body, as dicts, by the step's format."""
        f = step.fields.model_dump()
        if step.format == "json":
            import json

            data = json.loads(body)
            for item in (m.value for m in jsonpath(step.results).find(data)):
                yield {k: _json_get(item, v) for k, v in f.items()}
        elif step.format == "html":
            tree = HTMLParser(body)
            for node in tree.css(step.results):
                yield {k: _html_get(node, v) for k, v in f.items()}
        else:  # regex: fields map field -> group name, or "=text with {group}" for a template
            for m in re.finditer(step.results, body, re.S | re.I):
                g = {k: (v or "") for k, v in m.groupdict().items()}
                yield {
                    k: (_template(v, g) if v and v.startswith("=") else g.get(v) if v else None)
                    for k, v in f.items()
                }


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        v = v[0] if v else ""
    return re.sub(r"\s+", " ", str(v)).strip()


def _template(spec: str, groups: dict[str, str]) -> str:
    try:
        return spec[1:].format(**groups)
    except (KeyError, IndexError, ValueError):
        return spec[1:]
