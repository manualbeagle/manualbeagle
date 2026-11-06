"""Brand aliases and model-number variants.

Model numbers are the hardest input: LRFXS2503S, LRFXS2503S/00 and LRFXS2503S.ASTEUS can be the same fridge.
Adapters add their brand's own suffix rules; this module holds the rules every brand shares.
"""

from __future__ import annotations

import re
import unicodedata

from .models import Query

# Shared alias data: what people type -> the brand an adapter answers to. Adapters add their own aliases too.
# Keep a maker distinct from a similarly named one: "Mitsubishi" means Mitsubishi Electric (HVAC), not Motors.
BRAND_ALIASES: dict[str, str] = {
    "honeywell home": "Resideo",
    "honeywell": "Resideo",
    "mitsubishi": "Mitsubishi Electric",
    "mitsubishi electric trane": "Mitsubishi Electric",
    "lg electronics": "LG",
    "samsung electronics": "Samsung",
    "dewalt": "DeWalt",
    "de walt": "DeWalt",
    "tp-link kasa": "TP-Link",
    "kasa": "TP-Link",
    "kasa smart": "TP-Link",
    "tplink": "TP-Link",
    "tp link": "TP-Link",
    "toyota motor": "Toyota",
    "ecoflow tech": "EcoFlow",
}


def clean(s: str) -> str:
    """Unicode-normalise (NFKC), trim and collapse whitespace."""
    s = unicodedata.normalize("NFKC", s).strip()
    return re.sub(r"\s+", " ", s)


def brand_key(brand: str) -> str:
    """A brand name reduced to lower-case words, for matching: 'TP-Link' -> 'tp link'."""
    return re.sub(r"[^a-z0-9]+", " ", clean(brand).lower()).strip()


def compact(model: str) -> str:
    """Upper case, no spaces or separators: MSZ-FH12NA -> MSZFH12NA."""
    return re.sub(r"[^A-Z0-9]", "", model.upper())


_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def split_year(model: str) -> tuple[str, int | None]:
    """'2021 RAV4 Hybrid' -> ('RAV4 Hybrid', 2021). Used for vehicles."""
    m = _YEAR.search(model)
    if not m:
        return model, None
    rest = clean((model[: m.start()] + " " + model[m.end() :]).strip(" ,-"))
    return rest, int(m.group(1))


def generic_variants(model: str, strip_suffix: list[str] | None = None) -> list[str]:
    """Most to least specific. The original first, then with market suffixes taken off."""
    m = clean(model).upper()
    out: list[str] = [m]

    def add(v: str) -> None:
        v = v.strip(" -./_")
        if v and len(v) >= 3 and v not in out:
            out.append(v)

    stripped = [re.sub(pat, "", m) for pat in strip_suffix or []]
    for v in stripped:
        add(v)
    # Common to many makers: a market or revision suffix after "/" or "." (LRFXS2503S/00, LRFXS2503S.ASTEUS)
    add(re.split(r"[/.]", m)[0])
    # A trailing "(...)" note someone copied from a label
    add(re.sub(r"\s*\(.*\)$", "", m))
    # Separators people type differently
    add(m.replace(" ", "-"))
    add(compact(m))
    # ...and for what an adapter's strip_suffix rules left ("LITTER-ROBOT 5 PRO" -> "LITTER-ROBOT 5" -> "LITTER-ROBOT-5")
    for v in stripped:
        add(v.strip().replace(" ", "-"))
        add(compact(v))
    return out


def normalise(
    q: Query, adapter_rules: list[str] | None = None, canonical: str | None = None, vehicle: bool = False
) -> Query:
    """Fill in a query's canonical brand and model variants, most specific first.

    `adapter_rules` are the adapter's `strip_suffix` regexes; `canonical` is the adapter's brand name; for a
    `vehicle`, a year typed with the model ("2022 Sienna") is split off into `year`.
    """
    brand = clean(q.brand)
    model, year = clean(q.model), q.year
    if vehicle and year is None:
        stripped, y = split_year(model)
        if y and stripped:
            model, year = stripped, y
    canon = canonical or BRAND_ALIASES.get(brand_key(brand)) or brand
    return q.model_copy(
        update={
            "brand": brand,
            "model": model,
            "year": year,
            "canonical_brand": canon,
            "variants": generic_variants(model, adapter_rules),
        }
    )
