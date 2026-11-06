"""Rules-only verification and ranking. AI plugins may add to a score; they never replace it."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from .adapters.base import guess_kind
from .models import Candidate, DocKind, Query
from .normalize import compact

# Sites that copy manuals. Their terms forbid automated access (or we can't tell), so a person opens them.
MIRRORS = {
    "manualslib.com",
    "manualzz.com",
    "manualsonline.com",
    "manua.ls",
    "manualowl.com",
    "all-guidesbox.com",
    "usermanual.wiki",
    "safemanuals.com",
    "manualsdir.com",
    "manualsbase.com",
    "manualzilla.com",
    "fccid.io",
    "device.report",
    "scribd.com",
    "yumpu.com",
    "dokumen.pub",
    "manualsfile.com",
    "manualsbrain.com",
    "manuals.plus",
    "manualshelf.com",
    "manualsnet.com",
}

RETAILERS = {
    "homedepot.com",
    "thdstatic.com",
    "lowes.com",
    "bestbuy.com",
    "ajmadison.com",
    "acwholesalers.com",
    "sylvane.com",
    "us-appliance.com",
    "appliancesconnection.com",
    "abt.com",
    "pcrichard.com",
    "build.com",
    "ferguson.com",
    "supplyhouse.com",
    "partselect.com",
    "repairclinic.com",
    "searspartsdirect.com",
    "encompass.com",
    "walmart.com",
    "target.com",
    "costco.com",
    "acmetools.com",
    "toolbarn.com",
    "northerntool.com",
}

_KIND_WEIGHT = {
    DocKind.owners_manual: 12,
    DocKind.install_guide: 7,
    DocKind.quick_start: 5,
    DocKind.maintenance_guide: 4,
    DocKind.navigation_manual: 4,
    DocKind.warranty: 2,
    DocKind.spec_sheet: 2,
    DocKind.parts_list: 2,
    DocKind.service_manual: 3,
    DocKind.energy_guide: 0,
    DocKind.other: 0,
}

_NON_ENGLISH = re.compile(
    r"(?:^|[^a-z])(es|esp|spa|spanish|espa[nñ]ol|fr|fra|fre|french|fran[cç]ais|de|ger|deu|german|deutsch|"
    r"it|ita|italian|pt|por|portugu[eê]s|nl|dutch|jp|jpn|japanese|zh|chinese|kr|kor|korean|ru|rus|russian|"
    r"pl|polish|ar|arabic|tr|turkish|cz|czech|sv|swedish|th|thai|vi|vietnamese)(?:$|[^a-z])",
    re.I,
)
_LANG_WORDS = re.compile(
    r"\b(manual del|manuel d|bedienungsanleitung|gebruiksaanwijzing|istruzioni|instrucciones)\b", re.I
)


# A host-name label that marks a maker's staging or test server: support.dev1.lutron.com, staging.example.com.
_STAGING_LABEL = re.compile(
    r"^(?:dev|devel|development|stg|stage|staging|test|testing|qa|uat|preprod|sandbox)\d*$", re.I
)

# Locale codes as they appear in a URL's path: /us/es/, /en-gb/, /hk_en/ (Samsung puts the country first).
_LANGS = {
    "en",
    "es",
    "fr",
    "de",
    "it",
    "pt",
    "nl",
    "ja",
    "zh",
    "ko",
    "ru",
    "pl",
    "ar",
    "tr",
    "cs",
    "sv",
    "da",
    "fi",
    "nb",
    "he",
    "th",
    "vi",
    "el",
    "hu",
    "ro",
}
# ISO 3166-1 country codes, less those that are common words in a URL's path (tv, me, id, my, at, to...).
_COUNTRY_CODES = """
    ae af ag al ao ar au aw az ba bb bd be bf bg bh bi bj bn bo br bs bt bw bz ca cd cf cg ch ci ck cl cm cn co
    cr cu cv cy cz de dj dk dm dz ec ee eg er es et fi fj fr ga gb gd ge gh gm gn gq gr gt gw gy hk hn hr ht hu
    ie il in iq ir it jm jo jp ke kg kh ki km kn kr kw kz la lb lc li lk lr ls lt lu lv ly ma mc mg mk ml mm mn
    mo mr mt mu mv mw mx mz na ne ng ni nl no np nz om pa pe pg ph pk pl pr ps pt pw py qa ro rs ru rw sa sb sc
    sd se sg si sk sl sn sr ss sv sy sz td tg th tj tl tm tn tr tt tw tz ua ug uk us uy uz va vc ve vn vu ye za
    zm zw
"""
_REGIONS = set(_COUNTRY_CODES.split())


def is_staging(host: str) -> bool:
    """Whether a host is a staging or test server by its name (a label such as dev1, staging or qa below the domain)."""
    labels = host.lower().rstrip(".").split(".")[:-2]
    return any(_STAGING_LABEL.match(label) for label in labels)


def url_locale(url: str) -> tuple[str | None, str | None]:
    """The language and country a URL's first path segments name, such as ("es", "us") for /us/es/; None if not.

    A lone two-letter segment counts as a language if it is one (/es/), else as a country (/us/). A pair joined
    by - or _ may come either way round (/en-gb/, /hk_en/).
    """
    lang = region = None
    for seg in urlparse(url).path.lower().split("/")[1:4]:
        parts = re.split(r"[-_]", seg)
        if len(parts) == 1 and len(seg) == 2:
            if seg in _LANGS:
                lang = lang or seg
            elif seg in _REGIONS:
                region = region or seg
        elif len(parts) == 2 and all(len(x) == 2 for x in parts):
            a, b = parts
            if a in _LANGS and b in _REGIONS:
                lang, region = lang or a, region or b
            elif a in _REGIONS and b in _LANGS:
                lang, region = lang or b, region or a
    return lang, region


def host_matches(host: str, domains: set[str] | list[str]) -> bool:
    """Whether `host` is one of `domains` or a subdomain of one (any case; a trailing dot is ignored)."""
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def is_mirror(url: str) -> bool:
    """Whether the URL is on a known manual-mirror site (link only)."""
    return host_matches(urlparse(url).hostname or "", MIRRORS)


# Manuals cover a family with placeholders: "WM5700H*A", "MSZ-FH**NA", "LW8016ER/LW1016ER".
_WILDCARD = re.compile(r"[A-Z0-9][A-Z0-9-]*[*#?][A-Z0-9*#?-]*", re.I)


def wildcard_match(text: str, model: str) -> bool:
    """True when a placeholder token in text (from a PDF, so untrusted) covers the model.

    Tokens are capped (length, wildcards, count) before any regex is built from them, so text can't make the
    match slow.
    """
    target = compact(model)
    if len(target) > 40:
        return False
    for n, m in enumerate(_WILDCARD.finditer(text)):
        if n >= 200:
            break
        tok = re.sub(r"[*#?]+", "*", compact_keep_wild(m.group(0)))  # a run of wildcards counts as one
        if len(tok) > 32 or tok.count("*") > 4 or len(tok.replace("*", "")) < 4:
            continue
        pat = "[A-Z0-9]{0,3}".join(re.escape(part) for part in tok.split("*"))
        if re.fullmatch(pat + "[A-Z0-9]{0,4}", target):
            return True
    return False


def compact_keep_wild(s: str) -> str:
    """Upper case, letters and digits only, but keeping the wildcards * # ?."""
    return re.sub(r"[^A-Z0-9*#?]", "", s.upper())


def _model_pattern(model: str) -> re.Pattern[str]:
    """The model with optional separators between its characters, standing on its own.

    CL900 matches "CL-900" and "CL900_Instructions" but not "CL900E".
    """
    chars = [re.escape(ch) for ch in compact(model)]
    return re.compile(r"(?<![A-Z0-9])" + r"[\s./_-]?".join(chars) + r"(?![A-Z0-9])")


def model_in(text: str, variants: list[str]) -> int:
    """How strongly text names the model.

    3 = the full model on its own, 2 = a base variant on its own or a wildcard family, 1 = only inside a
    longer model number (CL900 in CL900E: maybe a sibling, maybe the same product with a suffix), 0 = not at
    all.
    """
    if not text:
        return 0
    text = unquote(text)
    up = text.upper()
    t = compact(text)
    weak = False
    for i, v in enumerate(variants):
        cv = compact(v)
        if len(cv) < 4 or cv not in t:
            continue
        if _model_pattern(v).search(up):
            return 3 if i == 0 else 2
        weak = True
    has_wild = "*" in text or "#" in text or "?" in text
    if has_wild and any(wildcard_match(text, v) for v in variants):
        return 2
    return 1 if weak else 0


def related_model(text: str, model: str) -> str | None:
    """A model number in text that is this model with a suffix, or this model's base, else None.

    RCHT9610WFW2004 for RCHT9610WF (a longer SKU of the same product); DCS380 for DCS380P1 (the tool in a
    kit). Siblings look the same (CL900E for CL900), so this is only trusted for an adapter's own result.
    """
    cm = compact(model).upper()
    if len(cm) < 5:
        return None
    for tok in set(re.findall(r"[A-Z0-9][A-Z0-9-]{3,24}", (text or "").upper())):
        ct = compact(tok)
        if ct == cm or len(ct) < 5 or not re.search(r"\d", ct):
            continue
        if ct.startswith(cm) or cm.startswith(ct):
            return tok
    return None


def score(c: Candidate, q: Query, maker_domains: list[str]) -> Candidate:
    """Score a candidate by the rules (maker's domain, model named, kind, PDF, language, country) and record why."""
    reasons: list[str] = []
    s = 0.0
    host = c.host

    if host_matches(host, maker_domains):
        s += 40
        reasons.append("maker's site +40")
    elif host_matches(host, RETAILERS):
        s += 15
        reasons.append("retailer +15")
    elif host_matches(host, MIRRORS):
        s += 3
        c.link_only = True
        reasons.append("manual mirror, link only +3")

    if is_staging(host):
        s -= 50
        reasons.append("a staging or test server -50")

    if c.source.startswith("adapter:"):
        s += 10
        reasons.append("from an adapter +10")
    elif c.source.startswith("page:"):
        s -= 15
        reasons.append("a support page to open, not a document -15")

    variants = q.variants or [q.model]
    m = max(
        model_in(c.title, variants), model_in(c.url, variants), *(model_in(x, variants) for x in c.models)
    )
    if m == 0 and c.snippet:
        m = min(model_in(c.snippet, variants), 2)
    if m == 3:
        s += 30
        reasons.append("names the model +30")
    elif m == 2:
        s += 20
        reasons.append("names a model variant +20")
    elif m == 1:
        s += 8
        reasons.append("names a longer model number that contains this one +8")
    elif c.source.startswith("adapter:"):
        # Adapters often return a family's documents without naming each model.
        s += 8
        reasons.append("adapter result for this model +8")

    if q.year and (str(q.year) in c.title or str(q.year) in c.url):
        s += 10
        reasons.append("names the year +10")

    kind = c.kind or guess_kind(f"{c.title} {unquote(urlparse(c.url).path)}")
    if kind:
        c.kind = kind
        w = _KIND_WEIGHT.get(kind, 0)
        if q.kind and kind == q.kind:
            w += 15
        if w:
            s += w
            reasons.append(f"{kind.value} +{w}")

    path = urlparse(c.url).path.lower()
    if path.endswith(".pdf"):
        s += 5
        reasons.append("a PDF +5")
    elif not c.link_only and not c.source.startswith("adapter:"):
        s -= 10
        reasons.append("not a PDF -10")

    lang = (c.language or "").lower()
    url_lang, url_region = url_locale(c.url)
    if lang and not lang.startswith(q.language) and lang not in ("multi", "multilingual"):
        s -= 15
        reasons.append(f"language {lang} -15")
    elif not lang and url_lang and url_lang != q.language:
        s -= 15
        reasons.append(f"URL names language {url_lang} -15")
    elif not lang and (_NON_ENGLISH.search(c.title) or _LANG_WORDS.search(c.title)):
        s -= 8
        reasons.append("title looks non-English -8")
    if url_region and q.region and url_region != q.region.lower():
        s -= 8
        reasons.append(f"URL names country {url_region} -8")

    c.score = s
    c.reasons = reasons
    return c


def rank(cands: list[Candidate], q: Query, maker_domains: list[str]) -> list[Candidate]:
    """Score every candidate, merge duplicates (keeping the higher score), and sort best first."""
    merged: dict[str, Candidate] = {}
    for c in cands:
        scored = score(c, q, maker_domains)
        k = c.key()
        if k not in merged or scored.score > merged[k].score:
            merged[k] = scored
    return sorted(merged.values(), key=lambda c: (-c.score, c.title))


def check_pdf_text(pages: list[str], q: Query) -> tuple[float, list[str]]:
    """Score a fetched PDF's text. The model in the text is the strongest signal."""
    reasons = []
    s = 0.0
    if not pages:
        return -20, ["no text extracted -20 (scan? try OCR)"]
    head = "\n".join(pages[:6])
    full = "\n".join(pages)
    m = max(model_in(head, q.variants or [q.model]), model_in(full, q.variants or [q.model]))
    if m == 3:
        s += 40
        reasons.append("PDF text names the model +40")
    elif m == 2:
        s += 25
        reasons.append("PDF text names a model variant +25")
    else:
        rel = related_model(full, q.model)
        if rel:
            s -= 5
            reasons.append(f"PDF text names a related model ({rel}) -5")
        else:
            s -= 10
            reasons.append("PDF text doesn't name the model -10")
    kind = guess_kind(pages[0][:300])  # the cover's heading, before body text mentions installing and parts
    if kind:
        reasons.append(f"first pages read as {kind.value}")
    if len(pages) >= 4:
        s += 3
    return s, reasons
