"""Maintenance suggestions read from manuals: tasks with an interval, approved by a person, filed in Homebox or Donetick.

How it works:

1. **Rules first.** `rules_for_page()` reads one page's text for a maintenance verb (clean, replace, descale,
   test...) with a time interval ("every 2 weeks", "monthly", "once a year"), in three shapes: a sentence
   ("Clean the door seal once a month"), a bullet under a part's heading ("Air filter / • Clean every 2 weeks"),
   and a row of a frequency table ("Filter  Once every week  Every 3 months" under "Cleaning Frequency
   Replacement Frequency"). Intervals under a week (daily, after each use) and counts of hours of use aren't
   suggested: a reminder in an inventory app is for weeks, months and years.
2. **An optional model adds to them.** With an `[ai]` model set up, `ai_for_page()` asks it about the pages with
   an interval on them, gives it the rules' findings to tidy, and keeps only answers whose quote is on the page
   and whose interval the page states. The model never removes a rule's finding.
3. **A person decides.** Suggestions are kept in the library's database (`Store`), one per item, task and
   interval, with the page and a quote. Each is approved (filed) or dismissed in the web UI; either way it's
   never suggested again.
4. **Filed where `[maintenance] target` says.** In Homebox (the default), `file_in_homebox()` adds a maintenance
   entry with a scheduled date to the item. Homebox (v0.27) has no repeating entries, so the entry's description
   says the interval and where it came from. In Donetick, `file_in_donetick()` makes a chore named for the
   appliance that repeats on the suggestion's interval, first due on the chosen date.

Nothing here writes to Homebox or Donetick except `file_in_homebox()` and `file_in_donetick()`, which only the web
UI's approve button calls.
"""

from __future__ import annotations

import calendar
import datetime as dt
import html
import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import AIConfig
from .models import KIND_LABELS, DocKind, Query
from .normalize import normalise

UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
MIN_DAYS = 7  # shorter than a week (daily, each use) isn't worth a reminder
MAX_DAYS = 3650
NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "eighteen": 18,
    "twenty-four": 24,
    "other": 2,
}
ADVERBS = {
    "weekly": (1, "week"),
    "bi-weekly": (2, "week"),
    "biweekly": (2, "week"),
    "fortnightly": (2, "week"),
    "monthly": (1, "month"),
    "bi-monthly": (2, "month"),
    "bimonthly": (2, "month"),
    "quarterly": (3, "month"),
    "semi-annually": (6, "month"),
    "semiannually": (6, "month"),
    "bi-annually": (6, "month"),
    "biannually": (6, "month"),
    "annually": (1, "year"),
    "yearly": (1, "year"),
    "daily": (1, "day"),
}
_NUM = r"\d{1,3}|" + "|".join(sorted(NUMBERS, key=len, reverse=True))
_UNIT = r"(?P<unit>day|week|month|year)s?\b"
INTERVAL = re.compile(
    r"(?:(?:at\s+least\s+|about\s+|approximately\s+)?(?:once\s+)?every\s+"
    rf"(?:(?P<n>{_NUM})(?:\s*(?:-|–|to|or)\s*(?P<n2>\d{{1,3}}))?\s*)?{_UNIT})"
    r"|(?:(?P<times>once(?:\s+or\s+twice)?|twice|\d|two|three|four)\s+(?:times\s+)?(?:a|per|each)\s+"
    r"(?P<unit2>day|week|month|year)\b)"
    rf"|\b(?P<adverb>{'|'.join(sorted(ADVERBS, key=len, reverse=True))})\b",
    re.I,
)
# Verbs a maintenance task starts with, and their past participles for "X should be cleaned".
VERBS = {
    "clean": "cleaned",
    "wash": "washed",
    "rinse": "rinsed",
    "wipe": "wiped",
    "vacuum": "vacuumed",
    "descale": "descaled",
    "decalcify": "decalcified",
    "delime": "delimed",
    "flush": "flushed",
    "drain": "drained",
    "empty": "emptied",
    "replace": "replaced",
    "change": "changed",
    "check": "checked",
    "inspect": "inspected",
    "test": "tested",
    "lubricate": "lubricated",
    "oil": "oiled",
    "grease": "greased",
    "tighten": "tightened",
    "sharpen": "sharpened",
    "calibrate": "calibrated",
    "service": "serviced",
    "charge": "charged",
    "recharge": "recharged",
    "exercise": "exercised",
    "blow": "blown",
    "rotate": "rotated",
    "run": "run",
    "remove": "removed",
}
# `run` and `remove` are maintenance only with these words after them ("run the tub clean cycle", "remove dust").
NEEDS_OBJECT = {
    "run": r"clean|cleaning|self-clean|descal|rins",
    "remove": r"dust|dirt|lint|debris|scale|residue|hair",
}
_VERB_RE = "|".join(sorted(VERBS, key=len, reverse=True))
_PAST_RE = "|".join(sorted({v for v in VERBS.values()}, key=len, reverse=True))
ACTIVE = re.compile(
    rf"(?:^|[,;:(]\s*|\b(?:and|then|or|please|should|must|to|always|also|regularly)\s+)(?P<verb>{_VERB_RE})\b",
    re.I,
)
PASSIVE = re.compile(
    rf"(?P<subj>(?:[A-Za-z][\w/-]*\s+){{1,5}}?)(?:should|must|needs?\s+to|has\s+to|have\s+to|is\s+to|are\s+to|"
    rf"to|will\s+need\s+to)\s+be\s+(?:\w+ly\s+)?(?P<past>{_PAST_RE})\b",
    re.I,
)
# Where an object stops: the words after it say how, why or when.
STOP = re.compile(
    r"\s(?:with|using|by|to\s+(?:prevent|keep|maintain|ensure|remove|avoid|help)|so|in\s+order|if|when|because|"
    r"for|at\s+least|about|approximately|once|or\s+(?:when|as|more)|as\s+(?:needed|required|necessary)|"
    r"and\s+(?:consider|then|if|make|adjust)|"
    r"after|before|during|while|every|unless|depending)\b",
    re.I,
)
BULLET = re.compile(r"^\s*(?:[•●▪■◆◦‣∙·\-\*–Ŏ%$✓►▶]|l(?=\s+[A-Z])|\?|\d{1,2}[.)]?(?=\s+[A-Z]))\s+")
# A frequency table's header names its columns: "Part  Cleaning Frequency  Replacement Frequency".
COLUMN = re.compile(r"\b(clean|replace|inspect|check|lubricat|chang|wash)\w*", re.I)
CELL = re.compile(
    INTERVAL.pattern + r"|(?P<dash>(?<!\w)[-–—](?!\w))|(?P<each>(?:after\s+)?each\s+use|as\s+needed)", re.I
)
NOT_TASKS = re.compile(
    r"\b(?:warrant\w*|timer|report|subscription|statement|guarantee|newsletter|update[sd]?\s+automatically|"
    r"firmware|app\s+will|days?\s+of\s+the\s+week)\b",
    re.I,
)
# Words that make a line a sentence rather than a part's name.
NOT_HEADING_WORDS = {
    "to", "be", "is", "are", "can", "cannot", "will", "if", "as", "such", "you", "it", "may", "should", "must",
    "do", "not", "when", "press", "use", "tap", "purchase", "see", "please", "make", "sure", "which", "that",
    "this", "has", "have", "was", "were", "page", "for", "from", "with", "by", "every", "each", "first", "item",
}  # fmt: skip
NOT_HEADINGS = {
    "cleaning",
    "maintenance",
    "care",
    "instructions",
    "instructions:",
    "notes",
    "note",
    "warning",
    "caution",
    "important",
    "cleaning and maintenance",
    "care and maintenance",
    "routine maintenance",
}
ARTICLES = re.compile(r"^(?:the|a|an|your|all|any|each)\s+", re.I)
MAX_QUOTE = 300


@dataclass(frozen=True)
class Interval:
    """A repeat interval: every `n` `unit`s (day, week, month or year), with the words the manual used."""

    n: int
    unit: str
    text: str = ""

    @property
    def days(self) -> int:
        """Roughly how many days apart (a month is 30), for sorting and limits."""
        return self.n * UNIT_DAYS[self.unit]

    @property
    def label(self) -> str:
        """The interval in plain words: "every 2 weeks", "every month"."""
        return f"every {self.unit}" if self.n == 1 else f"every {self.n} {self.unit}s"

    def after(self, start: dt.date) -> dt.date:
        """The date one interval after `start` (months and years by the calendar)."""
        if self.unit in ("day", "week"):
            return start + dt.timedelta(days=self.days)
        months = self.n * (12 if self.unit == "year" else 1)
        y, m = divmod(start.month - 1 + months, 12)
        year, month = start.year + y, m + 1
        return dt.date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def _number(word: str | None) -> int | None:
    if not word:
        return None
    word = word.lower()
    return int(word) if word.isdigit() else NUMBERS.get(word)


def _interval(m: re.Match) -> Interval | None:
    g = m.groupdict()
    text = re.sub(r"\s+", " ", m.group(0)).strip()
    if g.get("adverb"):
        n, unit = ADVERBS[g["adverb"].lower()]
        return Interval(n, unit, text)
    if g.get("unit"):
        if g.get("n") and _number(g["n"]) is None:
            return None
        return Interval(_number(g.get("n")) or 1, g["unit"].lower(), text)
    if g.get("unit2"):
        word = g["times"].lower().split()[0]  # "once or twice a month" is once a month
        times = {"once": 1, "twice": 2, "two": 2, "three": 3, "four": 4}.get(word) or int(word)
        unit, n = g["unit2"].lower(), 1
        # Twice a year is every 6 months; twice a month every 2 weeks; more often than weekly is too often.
        days = UNIT_DAYS[unit] / times
        if days >= 300:
            return Interval(1, "year", text)
        if days >= 28:
            return Interval(round(days / 30), "month", text)
        if days >= 7:
            return Interval(round(days / 7), "week", text)
        return Interval(n, "day", text)
    return None


def intervals(text: str) -> list[tuple[Interval, int, int]]:
    """Every interval in `text`, with where it starts and ends. "Every 3-6 months" is every 3 months."""
    out = []
    for m in INTERVAL.finditer(text):
        iv = _interval(m)
        if iv:
            out.append((iv, m.start(), m.end()))
    return out


def parse_interval(text: str) -> Interval | None:
    """The first interval in `text` that's worth a reminder (a week to ten years), or None."""
    return next((iv for iv, _, _ in intervals(text or "") if MIN_DAYS <= iv.days <= MAX_DAYS), None)


@dataclass
class Found:
    """A maintenance task found on a page: what to do, how often, and the words that say so."""

    task: str
    interval: Interval
    page: int
    quote: str
    source: str = "rules"

    @property
    def key(self) -> str:
        """The task's identity within one item: its words without articles, case or punctuation."""
        return task_key(self.task)


def task_key(task: str) -> str:
    """A task's words in lower case, without articles, punctuation, or a plural s, for finding repeats."""
    words = re.findall(r"[a-z0-9]+", task.lower())
    words = [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words]
    return " ".join(w for w in words if w not in {"the", "a", "an", "your", "all", "any", "each", "of"})


def clean_text(text: str) -> str:
    """A page's text with PDF extraction's debris mended: split ligatures ("fi lter"), soft hyphens, CR LF."""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[￾­‐]\n", "", t)
    t = t.replace("￾", "").replace("­", "")
    t = re.sub(r"\b(f[il]|ff) (?=[a-z]{2,})", r"\1", t)
    return t


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _quote(s: str) -> str:
    s = _squash(s)
    return s if len(s) <= MAX_QUOTE else s[: MAX_QUOTE - 1].rsplit(" ", 1)[0] + "…"


def _part(heading: str) -> str:
    """A part's name from a heading, as the object of a task: "Air filter (Nano platinum filter)" -> "air filter"."""
    h = re.sub(r"\([^)]*\)?", "", heading).strip(" :-–•")
    if len(h.split()) > 1:  # "Cleaning the Tape Cutter" -> "Tape Cutter"
        h = re.sub(r"^\w+ing\s+(?:the|your)\s+", "", h, flags=re.I)
    h = ARTICLES.sub("", h)
    words = [
        w if (w.isupper() and len(w) > 1) or any(c.isdigit() for c in w) else w.lower() for w in h.split()
    ]
    return " ".join(words)


def _object(rest: str) -> str:
    """The object after a verb: up to the words that say how or when, at most eight words."""
    rest = _squash(rest)
    m = STOP.search(" " + rest)
    if m:
        rest = rest[: max(0, m.start() - 1)]
    rest = re.split(r"[.,;:!?()\[\]•]", rest, maxsplit=1)[0]
    words = rest.split()[:8]
    if any((len(w) == 1 and w.lower() not in "ai") or w.isdigit() for w in words):
        return ""  # table debris ("Change o o 59")
    while words and words[-1].lower() in {"and", "or", "the", "a", "an", "at", "of", "to", "in", "on", "it"}:
        words.pop()
    return " ".join(words)


def _task(verb: str, obj: str) -> str:
    obj = re.sub(r"^(?:your|its|their)\s+", "the ", obj.strip(), flags=re.I)
    task = f"{verb.capitalize()} {obj}".strip()
    return task[:1].upper() + task[1:]


def _heading(line: str) -> bool:
    """Whether a line looks like a part's heading: short, not a sentence, not a bullet, with a noun."""
    s = line.strip()
    if not s or len(s) > 60 or BULLET.match(s) or s.endswith((".", "!", "?", ",")):
        return False
    words = s.split()
    if not 1 <= len(words) <= 7 or not re.search(r"[A-Za-z]{3}", s):
        return False
    # "Drain pan" is a part; "Clean the filter" is an instruction.
    instruction = re.match(rf"(?:{_VERB_RE})\s+(?:the|a|an|your|it|them|all|any|and|every|once)\b", s, re.I)
    if s.lower().strip(":") in NOT_HEADINGS or intervals(s) or instruction:
        return False
    bare = re.sub(r"\([^)]*\)?", " ", s)
    if "," in bare or {w.lower() for w in re.findall(r"[A-Za-z]+", bare)} & NOT_HEADING_WORDS:
        return False
    return s[0].isupper()


def _segments(text: str) -> Iterator[tuple[str, str, bool]]:
    """The page as segments (a bullet, or a run of wrapped lines), each with the nearest heading above it.

    Each comes with whether it's a bullet. A line that is only an interval ("EVERY MONTH", "Every 3 months:")
    is a segment of its own.
    """
    heading, buf = "", []
    bullet = False

    def flush() -> Iterator[tuple[str, str, bool]]:
        if buf:
            yield " ".join(buf), heading, bullet
            buf.clear()

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            yield from flush()
            continue
        if BULLET.match(line):
            yield from flush()
            buf.append(BULLET.sub("", line))
            bullet = True
            continue
        if _interval_only(line):
            yield from flush()
            yield line, heading, False
            continue
        if line.isupper() and len(line) > 3:  # a section title ("SERVICE SCHEDULE") stands alone
            yield from flush()
            heading = line if _heading(line) else ""
            continue
        if not buf:
            bullet = False
        if _heading(line) and (not buf or buf[-1].endswith((".", ":", "!", "?"))):
            yield from flush()
            heading = line
            continue
        if buf and buf[-1].endswith((".", ":", "!", "?")):
            yield from flush()
            bullet = False
        buf.append(line)
    yield from flush()


def _interval_only(segment: str) -> Interval | None:
    """The interval when a segment is nothing else ("Every 3 months:"), else None."""
    found = intervals(segment)
    if len(found) == 1 and _squash(segment).rstrip(":").lower() == found[0][0].text.lower():
        return found[0][0]
    return None


def _sentences(segment: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9•])", segment) if s.strip()]


def _verb_ok(verb: str, after: str) -> bool:
    need = NEEDS_OBJECT.get(verb.lower())
    return need is None or re.match(rf"\s*(?:\w+\s+){{0,3}}?(?:{need})", after, re.I) is not None


def _from_sentence(sentence: str, heading: str, page: int, pending: Interval | None) -> list[Found]:
    """Tasks in one sentence: each interval goes with the nearest maintenance verb before it (else after it)."""
    if NOT_TASKS.search(sentence):
        return []
    ivs = [(iv, a, b) for iv, a, b in intervals(sentence) if MIN_DAYS <= iv.days <= MAX_DAYS]
    if not ivs and pending is None:
        return []
    verbs = [
        (m.start("verb"), m.group("verb"), sentence[m.end("verb") :])
        for m in ACTIVE.finditer(sentence)
        if _verb_ok(m.group("verb"), sentence[m.end("verb") :])
    ]
    passives = list(PASSIVE.finditer(sentence))
    out = []
    last_end = -1
    for iv, start, end in ivs or [(pending, 0, 0)]:
        assert iv is not None
        # "every two weeks, or at least once a month": the second is the same task's fallback.
        if last_end >= 0 and re.fullmatch(r"[\s,;]*or\s+(?:at\s+least\s+)?", sentence[last_end:start], re.I):
            continue
        last_end = end
        near = sentence[:start]
        before = [v for v in verbs if v[0] < start and len(near[v[0] :].split()) <= 14]
        # A verb after the interval only right after it ("About once every year, wipe the blades"), or, for an
        # interval from a line above, among the first words.
        after = [
            v for v in verbs if v[0] >= end and re.fullmatch(r"[\s,:]*(?:\w+\s+)?", sentence[end : v[0]])
        ]
        task = ""
        pas = [p for p in passives if p.start() < start and len(near[p.end() :].split()) <= 14]
        if pas and (not before or pas[-1].start("past") > before[-1][0]):
            p = pas[-1]
            past = p.group("past").lower()
            # "should be checked once a month and cleaned once a year": the later verb goes with the later interval.
            more = re.findall(rf"\band\s+(?:\w+ly\s+)?({_PAST_RE})\b", sentence[p.end() : start], re.I)
            past = (more[-1] if more else past).lower()
            base = next(b for b, pp in VERBS.items() if pp == past)
            task = _task(base, "the " + _part(_subject(p.group("subj")))) if _subject(p.group("subj")) else ""
        elif before or after:
            at, verb, rest = before[-1] if before else after[0]
            obj = _object(rest[: start - at - len(verb)] if at < start else rest)
            if verb.lower() == "remove" and heading:  # "Remove dirt by a vacuum cleaner" under a part's name
                task = _task("clean", "the " + _part(heading))
            elif obj and not re.match(
                r"(?:as|if|when|it|them|this|these|required|needed|to|that)\b", obj, re.I
            ):
                task = _task(verb, obj)
            elif heading:
                task = _task(verb, "the " + _part(heading))
        if task and len(task.split()) >= 2:
            out.append(Found(task, iv, page, _quote((heading + ": " if heading else "") + sentence)))
    return out


def _subject(subj: str) -> str:
    """The subject of "X should be cleaned": its last words that aren't a pronoun, modal or verb."""
    words = re.sub(r"^\s*\d+[.)]?\s*", "", subj).split()
    keep: list[str] = []
    for w in reversed(words):
        if w.lower() in NOT_HEADING_WORDS or len(keep) >= 5:
            break
        keep.insert(0, w)
    return ARTICLES.sub("", " ".join(keep))


ROW = re.compile(
    rf"^(?P<part>[A-Z][A-Za-z /&-]{{2,40}}?)\s+(?P<verb>(?i:{_VERB_RE}))\b[\s-]*(?:\w+\s+)?(?=(?i:every|once)\b)"
)


def _from_rows(lines: list[str], page: int) -> list[Found]:
    """Rows of a maintenance schedule: "Fuel tube  Check  Every 2 years"."""
    out = []
    for line in lines:
        s = _squash(line)
        m = ROW.match(s)
        if not m or not _heading(m.group("part")):
            continue
        iv = parse_interval(s[m.end() :])
        if iv:
            out.append(Found(_task(m.group("verb"), "the " + _part(m.group("part"))), iv, page, _quote(s)))
    return out


def _from_tables(lines: list[str], page: int) -> list[Found]:
    """Rows of a frequency table: a part's name, then one interval (or a dash) per column the header names."""
    out = []
    columns: list[str] = []
    header = ""
    for line in lines:
        s = line.strip()
        if re.search(r"\bfrequency\b", s, re.I) and COLUMN.search(s) and not intervals(s):
            columns = [c.lower() for c in COLUMN.findall(s)]
            header = s
            continue
        if not columns:
            continue
        cells = list(CELL.finditer(s))
        if not cells or cells[0].start() == 0:
            continue
        part = s[: cells[0].start()].strip()
        if not _heading(part) or not re.fullmatch(r"[A-Z][A-Za-z /&-]{1,40}", part):
            continue
        for col, cell in zip(columns, cells, strict=False):
            iv = _interval(cell) if cell.group("dash") is None and cell.group("each") is None else None
            if iv is None or not MIN_DAYS <= iv.days <= MAX_DAYS:
                continue
            verb = next(v for v in VERBS if v.startswith(col[:5]) or col.startswith(v[:5]))
            out.append(Found(_task(verb, "the " + _part(part)), iv, page, _quote(f"{header}\n{s}")))
    return out


def rules_for_page(text: str, page: int) -> list[Found]:
    """The maintenance tasks with an interval on one page, by rules alone, each once."""
    text = clean_text(text)
    found: list[Found] = []
    pending: Interval | None = None
    for segment, heading, bullet in _segments(text):
        # "EVERY MONTH" or "Every 3 months:" on its own line: the interval for the bullets that follow it (or for
        # the paragraph right after it).
        alone = _interval_only(segment)
        if alone is not None:
            pending = alone if MIN_DAYS <= alone.days <= MAX_DAYS else None
            continue
        sentences = _sentences(segment)
        for sentence in sentences:
            found += _from_sentence(sentence, heading, page, None)
        if pending is not None and sentences and not intervals(segment):
            found += _from_sentence(sentences[0], heading, page, pending)
        if not bullet:
            pending = None
    lines = text.split("\n")
    found += _from_tables(lines, page) + _from_rows(lines, page)
    return dedupe(found)


def dedupe(found: Iterable[Found]) -> list[Found]:
    """Each task and interval once, the first kept."""
    seen, out = set(), []
    for f in found:
        k = (f.key, f.interval.n, f.interval.unit)
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def rules_for_document(pages: list[str]) -> list[Found]:
    """Every page's tasks (pages counted from 1), each task and interval once."""
    return dedupe(f for i, text in enumerate(pages) for f in rules_for_page(text, i + 1))


def candidate_pages(pages: list[str], found: list[Found], limit: int = 12) -> list[int]:
    """The pages worth asking a model about: those the rules found something on, then others with an interval."""
    picked = sorted({f.page for f in found})
    for i, text in enumerate(pages):
        if len(picked) >= limit:
            break
        if i + 1 not in picked and parse_interval(clean_text(text)) and COLUMN.search(text):
            picked.append(i + 1)
    return sorted(picked[:limit])


# The optional model


PROMPT = """You are reading page {page} of the {what}.
List the maintenance tasks on this page that the owner should repeat on a schedule given as a time interval in days, weeks, months or years.
Leave out tasks done after each use, as needed, or only by hours of use, and anything that is not maintenance (timers, schedules, warranties, settings).
Rules already found these on the page; repeat each one that is a real task, with clearer wording if it helps:
{found}
For each task give:
- "task": a short instruction, at most 8 words, such as "Clean the air filter";
- "interval": the interval as the page says it, such as "every 2 weeks";
- "quote": the exact words on the page that say it, at most 30 words.
Answer with only a JSON object: {{"tasks": [{{"task": "...", "interval": "...", "quote": "..."}}]}}. If there are none, answer {{"tasks": []}}.

The page:
\"\"\"
{text}
\"\"\""""
MAX_PAGE_CHARS = 6000


class ModelError(Exception):
    """The model couldn't be asked, or its answer couldn't be used."""


SYSTEM = (
    "You read pages of appliance manuals and list the maintenance tasks they schedule. You quote the page exactly "
    "and never invent a task or an interval. You answer with only JSON."
)


def model_label(cfg: AIConfig) -> str:
    """The provider and model as the page names them ("ollama gemma3"), or "" when no model is set up."""
    from .ask import AskError, make_model

    try:
        model = make_model(cfg, None)
    except AskError:
        return ""
    return model.label if model else ""


def ask_model(cfg: AIConfig, http: Any, prompt: str) -> str:
    """Send one prompt to the `[ai]` text model (Ollama or Anthropic, `ask.make_model`) and return its reply.

    Raises ModelError when there's no model, or it can't be asked.
    """
    from .ask import AskError, make_model

    try:
        model = make_model(cfg, http)
        if model is None:
            raise ModelError("no model is set up in [ai]")
        return model.complete(SYSTEM, prompt)
    except AskError as e:
        raise ModelError(str(e)) from None


def _words(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", clean_text(s).lower()))


def quote_on_page(quote: str, page_text: str) -> bool:
    """Whether a model's quote is on the page: word for word, ignoring case, spacing and punctuation."""
    q = _words(quote)
    return len(q.split()) >= 2 and q in _words(page_text)


def parse_tasks(answer: str) -> list[dict]:
    """The task list in a model's answer: the "tasks" of its first JSON object (or a bare list)."""
    m = re.search(r"[\[{].*[\]}]", answer or "", re.S)
    if not m:
        raise ModelError("the model's answer had no JSON in it")
    try:
        data = json.loads(m.group(0))
    except ValueError:
        raise ModelError("the model's answer wasn't valid JSON") from None
    tasks = data.get("tasks") if isinstance(data, dict) else data
    return [t for t in tasks or [] if isinstance(t, dict)] if isinstance(tasks, list) else []


def ai_for_page(
    ask: Callable[[str], str], what: str, page: int, text: str, found: list[Found], label: str
) -> list[Found]:
    """Ask the model about one page; return the rules' findings tidied by it, plus what it added.

    A model's task is kept only when its quote is on the page and its interval is one the page states (a week to
    ten years). One that matches a rule's finding (same interval, and overlapping quotes with a word of the task
    in common, or the same task) replaces that finding's wording, once; the rules' findings the model left out
    are kept as they were.
    """
    text = clean_text(text)
    listed = "\n".join(f"- {f.task} ({f.interval.label})" for f in found) or "- (none)"
    prompt = PROMPT.format(page=page, what=what, found=listed, text=text[:MAX_PAGE_CHARS])
    stated = {(iv.n, iv.unit) for iv, _, _ in intervals(text)}
    out = list(found)
    tidied: set[int] = set()
    for t in parse_tasks(ask(prompt)):
        task = _squash(str(t.get("task") or ""))[:120]
        quote = _squash(str(t.get("quote") or ""))
        iv = parse_interval(str(t.get("interval") or "")) or parse_interval(quote)
        if not task or len(task.split()) < 2 or iv is None or (iv.n, iv.unit) not in stated:
            continue
        if not quote_on_page(quote, text) or NOT_TASKS.search(quote):
            continue
        task = task[:1].upper() + task[1:].rstrip(".")
        theirs = set(task_key(task).split()[1:])
        same = [
            i
            for i, f in enumerate(out)
            if i not in tidied
            and f.source == "rules"
            and (f.interval.n, f.interval.unit) == (iv.n, iv.unit)
            and (
                (_overlap(f.quote, quote) and set(task_key(f.task).split()[1:]) & theirs)
                or task_key(f.task).split()[1:] == task_key(task).split()[1:]
            )
        ]
        same.sort(key=lambda i: not _overlap(out[i].quote, quote))  # the finding the model quoted, first
        if same:
            f = out[same[0]]
            tidied.add(same[0])
            out[same[0]] = Found(task, f.interval, page, f.quote, source=f"rules+{label}")
        else:
            out.append(Found(task, iv, page, _quote(quote), source=label))
    return dedupe(out)


def _overlap(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and (wa in wb or wb in wa)


# Which inventory items a document belongs to


def _model_key(model: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (model or "").lower())


def _brand_key(brand: str) -> str:
    q = normalise(Query(brand=brand or "?", model="x"))
    return (q.canonical_brand or brand or "").lower()


def match_items(brand: str, model: str, items: Iterable[Any]) -> list[Any]:
    """The inventory items with this brand (or an alias of it) and model, ignoring case, spaces and dashes."""
    b, m = _brand_key(brand), _model_key(model)
    if not m:
        return []
    return [i for i in items if _model_key(i.model) == m and _brand_key(i.manufacturer) == b]


# The store: suggestions and decisions, in the library's database


SCHEMA = """
CREATE TABLE IF NOT EXISTS maintenance (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,
    task_key TEXT NOT NULL,
    every_n INTEGER NOT NULL,
    every_unit TEXT NOT NULL,
    task TEXT NOT NULL,
    interval_text TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    page INTEGER NOT NULL,
    quote TEXT NOT NULL,
    brand TEXT,
    model TEXT,
    item_id TEXT NOT NULL DEFAULT '',
    item_name TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    scheduled TEXT,
    homebox_entry TEXT,
    error TEXT,
    found_at REAL,
    decided_at REAL,
    filed_in TEXT,
    donetick_chore TEXT,
    UNIQUE (subject, task_key, every_n, every_unit)
);
"""
STATUSES = ("new", "filed", "dismissed")
TARGETS = ("homebox", "donetick")
# Columns added after the table was first made, for libraries made before them.
ADDED_COLUMNS = {"filed_in": "TEXT", "donetick_chore": "TEXT"}


@dataclass
class Suggestion:
    """One stored suggestion: the task, its interval, where it was read, the item, and what was decided."""

    id: int
    task: str
    interval: str
    every_n: int
    every_unit: str
    sha256: str
    page: int
    quote: str
    brand: str
    model: str
    item_id: str
    item_name: str
    source: str
    status: str
    scheduled: str | None = None
    homebox_entry: str | None = None
    error: str | None = None
    found_at: float | None = None
    decided_at: float | None = None
    title: str = ""
    kind: str = ""
    filed_in: str | None = None  # homebox | donetick, once filed (None for one filed before Donetick existed)
    donetick_chore: str | None = None

    @property
    def every(self) -> Interval:
        """The interval as an `Interval`."""
        return Interval(self.every_n, self.every_unit, self.interval)

    def first_date(self, today: dt.date | None = None) -> dt.date:
        """The default first date: one interval from today."""
        return self.every.after(today or dt.date.today())

    def public(self) -> dict:
        """The suggestion as JSON, with its interval in words and its default first date."""
        d = asdict(self)
        d["every"] = self.every.label
        d["first_date"] = self.first_date().isoformat()
        if self.status == "filed" and not self.filed_in:
            d["filed_in"] = "homebox"
        return d


@dataclass
class RunCount:
    """What one suggest run did for one document."""

    sha256: str
    brand: str
    model: str
    title: str = ""
    found: int = 0
    added: int = 0
    pages_asked: int = 0
    items: list[str] = field(default_factory=list)
    error: str = ""


class Store:
    """Suggestions and decisions in the library's SQLite database (the `maintenance` table)."""

    def __init__(self, lib: Any) -> None:
        self.lib = lib
        self.db = lib.db
        self.db.executescript(SCHEMA)
        have = {row[1] for row in self.db.execute("PRAGMA table_info(maintenance)")}
        for column, kind in ADDED_COLUMNS.items():
            if column not in have:
                self.db.execute(f"ALTER TABLE maintenance ADD COLUMN {column} {kind}")
        self.db.commit()

    def add(self, f: Found, sha: str, brand: str, model: str, item_id: str = "", item_name: str = "") -> bool:
        """Keep a finding for an item (or, with no item, for the brand and model). False when it was known.

        Known means the same item (or brand and model), task and interval in any state: a dismissed suggestion
        isn't suggested again. So does the same page, quote and interval: a model's rewording of a rule's finding
        (source `rules+<model>`) updates the wording of the suggestion while it's still waiting, rather than
        adding a second one. A finding for an item takes over the same suggestion still waiting without one.
        """
        subject = item_id or f"{brand}|{model}".lower()
        ident = (task_key(f.task), f.interval.n, f.interval.unit)
        if self.db.execute(
            "SELECT 1 FROM maintenance WHERE subject=? AND task_key=? AND every_n=? AND every_unit=?",
            (subject, *ident),
        ).fetchone():
            return False
        if item_id:
            cur = self.db.execute(
                "UPDATE maintenance SET subject=?, item_id=?, item_name=? WHERE subject=? AND task_key=? "
                "AND every_n=? AND every_unit=? AND status='new'",
                (subject, item_id, item_name, f"{brand}|{model}".lower(), *ident),
            )
            if cur.rowcount:
                self.db.commit()
                return False
        same = self.db.execute(
            "SELECT id, status, source FROM maintenance WHERE subject IN (?, ?) AND sha256=? AND page=? AND quote=? "
            "AND every_n=? AND every_unit=?",
            (subject, f"{brand}|{model}".lower(), sha, f.page, f.quote, f.interval.n, f.interval.unit),
        ).fetchone()
        if same:
            sid, status, source = same
            if status == "new" and f.source.startswith("rules+") and source == "rules":
                try:
                    self.db.execute(
                        "UPDATE maintenance SET task=?, task_key=?, source=? WHERE id=?",
                        (f.task, ident[0], f.source, sid),
                    )
                    self.db.commit()
                except sqlite3.IntegrityError:  # that wording is another suggestion already
                    self.db.rollback()
            return False
        self.db.execute(
            "INSERT INTO maintenance (subject, task_key, every_n, every_unit, task, interval_text, sha256, page, "
            "quote, brand, model, item_id, item_name, source, found_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                subject,
                *ident,
                f.task,
                f.interval.text or f.interval.label,
                sha,
                f.page,
                f.quote,
                brand,
                model,
                item_id,
                item_name,
                f.source,
                time.time(),
            ),
        )
        self.db.commit()
        return True

    def list(self, status: str | None = "new", sha: str | None = None) -> list[Suggestion]:
        """Suggestions in one state (None: all), by item, then by how often, with their document's title."""
        sql = (
            "SELECT m.id, m.task, m.interval_text, m.every_n, m.every_unit, m.sha256, m.page, m.quote, m.brand, "
            "m.model, m.item_id, m.item_name, m.source, m.status, m.scheduled, m.homebox_entry, m.error, "
            "m.found_at, m.decided_at, coalesce(d.title, ''), coalesce(d.kind, ''), m.filed_in, m.donetick_chore "
            "FROM maintenance m LEFT JOIN documents d ON d.sha256 = m.sha256 WHERE 1=1"
        )
        args: list = []
        if status:
            sql += " AND m.status=?"
            args.append(status)
        if sha:
            sql += " AND m.sha256=?"
            args.append(sha)
        sql += (
            " ORDER BY coalesce(nullif(m.item_name, ''), m.brand || ' ' || m.model), m.brand, m.model, m.id"
        )
        rows = self.db.execute(sql, args).fetchall()
        out = [Suggestion(*r) for r in rows]
        return sorted(
            out, key=lambda s: ((s.item_name or f"{s.brand} {s.model}").lower(), s.every.days, s.id)
        )

    def get(self, sid: int) -> Suggestion | None:
        """One suggestion, or None."""
        return next((s for s in self.list(None) if s.id == sid), None)

    def counts(self) -> dict[str, int]:
        """How many suggestions are in each state."""
        n = dict.fromkeys(STATUSES, 0)
        for status, count in self.db.execute("SELECT status, count(*) FROM maintenance GROUP BY status"):
            n[status] = count
        return n

    def dismiss(self, sid: int) -> bool:
        """Mark a waiting suggestion dismissed, so it isn't suggested again. False unless it was waiting."""
        cur = self.db.execute(
            "UPDATE maintenance SET status='dismissed', decided_at=?, error=NULL WHERE id=? AND status='new'",
            (time.time(), sid),
        )
        self.db.commit()
        return cur.rowcount > 0

    def reopen(self, sid: int) -> bool:
        """Put a dismissed suggestion back among the waiting ones. False unless it was dismissed."""
        cur = self.db.execute(
            "UPDATE maintenance SET status='new', decided_at=NULL WHERE id=? AND status='dismissed'", (sid,)
        )
        self.db.commit()
        return cur.rowcount > 0

    def filed(
        self,
        sid: int,
        task: str,
        item_id: str,
        item_name: str,
        scheduled: str,
        entry: str,
        target: str = "homebox",
    ) -> None:
        """Record that a suggestion was approved and filed in `target`: Homebox's entry or Donetick's chore `entry`."""
        if target not in TARGETS:
            raise ValueError(f"target must be one of {', '.join(TARGETS)}")
        column = "homebox_entry" if target == "homebox" else "donetick_chore"
        self.db.execute(
            f"UPDATE maintenance SET status='filed', task=?, item_id=?, item_name=?, scheduled=?, {column}=?, "
            "filed_in=?, error=NULL, decided_at=? WHERE id=?",
            (task, item_id, item_name, scheduled, entry, target, time.time(), sid),
        )
        self.db.commit()

    def failed(self, sid: int, error: str) -> None:
        """Record why filing a suggestion failed; it stays waiting."""
        self.db.execute("UPDATE maintenance SET error=? WHERE id=?", (error[:500], sid))
        self.db.commit()


def document_items(
    lib: Any, sha: str, brand: str, model: str, items: list[Any] | None
) -> list[tuple[str, str]]:
    """The inventory items (id, name) a document belongs to: every brand and model it was fetched for, matched.

    With no inventory (`items` None), or no match, [] (the suggestion is kept for the brand and model).
    """
    if not items:
        return []
    pairs = {(brand, model)} | set(
        lib.db.execute("SELECT DISTINCT brand, model FROM links WHERE sha256=?", (sha,)).fetchall()
    )
    out: dict[str, str] = {}
    for b, m in pairs:
        for it in match_items(b, m, items):
            out.setdefault(it.id, it.name)
    return sorted(out.items(), key=lambda kv: kv[1])


def what_document(d: dict) -> str:
    """A document named for a prompt: "LG WM5700HVA owner's manual"."""
    try:
        kind = KIND_LABELS.get(DocKind(d.get("kind")), "manual")
    except ValueError:
        kind = "manual"
    return f"{d['brand']} {d['model']} {kind.lower()}"


def suggest(
    lib: Any,
    items: list[Any] | None = None,
    ask: Callable[[str], str] | None = None,
    label: str = "",
    sha: str | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> Iterator[RunCount]:
    """Read every document (or one, `sha`) for maintenance tasks and keep the new ones. Yields one count each.

    `items` are the inventory's items to match documents to (None: no inventory). `ask`, when given, sends a
    prompt to the optional model, named `label` in each suggestion's source; a model that fails on a page leaves
    that page to the rules and says so in the count's `error`.
    """
    store = Store(lib)
    docs = [d for d in lib.documents() if sha is None or d["sha256"] == sha]
    for d in docs:
        if cancelled():
            return
        count = RunCount(d["sha256"], d["brand"], d["model"], d.get("title") or "")
        pages = lib.page_texts(d["sha256"])
        found = rules_for_document(pages)
        if ask is not None:
            for p in candidate_pages(pages, found):
                if cancelled():
                    return
                here = [f for f in found if f.page == p]
                count.pages_asked += 1
                try:
                    tidied = ai_for_page(ask, what_document(d), p, pages[p - 1], here, label)
                except ModelError as e:
                    count.error = str(e)
                    continue
                found = [f for f in found if f.page != p] + tidied
            found = dedupe(sorted(found, key=lambda f: f.page))
        count.found = len(found)
        targets = document_items(lib, d["sha256"], d["brand"], d["model"], items)
        count.items = [name for _, name in targets]
        for f in found:
            for item_id, item_name in targets or [("", "")]:
                count.added += store.add(f, d["sha256"], d["brand"], d["model"], item_id, item_name)
        yield count


# Filing in Homebox


def description(s: Suggestion) -> str:
    """The Homebox entry's description: the interval, where it was read, the quote, and who filed it."""
    doc = s.title or f"{s.brand} {s.model} manual"
    text = (
        f"Repeat {s.every.label}. From {doc} ({s.brand} {s.model}), page {s.page}: “{s.quote}”\n"
        "Suggested by Manual Beagle and approved by a person. Homebox doesn't repeat entries: when this is done, "
        "add the next one."
    )
    return text[:2500]


def file_in_homebox(hb: Any, s: Suggestion, item_id: str, task: str, scheduled: dt.date) -> str:
    """Add an approved suggestion to a Homebox item as a maintenance entry; returns the entry's id.

    Raises HomeboxError when Homebox refuses.
    """
    entry = hb.add_maintenance(item_id, task[:255], description(s), scheduled.isoformat())
    return str((entry or {}).get("id") or "")


# Filing in Donetick


def chore_description(s: Suggestion, link: str = "") -> str:
    """The Donetick chore's description (HTML, as Donetick keeps it): the interval, where it was read, the quote.

    `link`, when given, is the web UI's address of the page the task was read on.
    """
    doc = html.escape(s.title or f"{s.brand} {s.model} manual")
    page = f'<a href="{html.escape(link)}">page {s.page}</a>' if link else f"page {s.page}"
    return (
        f"<p>Repeat {html.escape(s.every.label)}. From {doc} ({html.escape(f'{s.brand} {s.model}')}), {page}: "
        f"“{html.escape(s.quote)}”</p><p>Suggested by Manual Beagle and approved by a person.</p>"
    )[:5000]


def chore_name(appliance: str, task: str) -> str:
    """The chore's name: the appliance, then the task ("Window AC: Clean the filter")."""
    appliance, task = _squash(appliance), _squash(task)
    return f"{appliance}: {task}"[:255] if appliance else task[:255]


def file_in_donetick(
    client: Any, s: Suggestion, appliance: str, task: str, first: dt.date, cfg: Any, link: str = ""
) -> str:
    """Make an approved suggestion a Donetick chore that repeats on its interval; returns the chore's id.

    `client` is a `sinks.donetick.Donetick`, `cfg` the `[donetick]` table (label, due time, time zone, rolling,
    notify). The chore is named for the appliance, first due on `first` at the due time, and assigned to the
    token's user. Raises DonetickError when Donetick refuses.
    """
    from .sinks.donetick import due_at, interval_chore

    when = due_at(first, cfg.due_time, cfg.timezone)
    user = client.me()
    label = client.label_id(cfg.label) if cfg.label else None
    chore = interval_chore(
        chore_name(appliance, task),
        s.every_n,
        s.every_unit,
        when,
        chore_description(s, link),
        int(user["id"]),
        timezone=cfg.timezone,
        label_id=label,
        rolling=cfg.rolling,
        notify=cfg.notify,
    )
    return str(client.create_chore(chore))
