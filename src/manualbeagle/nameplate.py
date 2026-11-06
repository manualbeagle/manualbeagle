"""Reading an appliance's rating plate (nameplate) from a photo, with rules only.

A rating plate is the sticker or metal plate inside a door or on the back of an appliance that gives its brand,
model and serial number. `ocr_image()` runs Tesseract on the photo, and `read_plate()` finds the model and serial
by their labels (MODEL, M/N, MOD. NO., S/N, SERIAL) and guesses the brand from the adapters' names and aliases plus
a list of common makers. It only suggests: the person confirms or corrects the brand and model before a lookup.

An optional vision model (`vision.py`) can add its own reading on top; nothing here needs one.

The photo itself is checked by `image_type()` (JPEG or PNG, by its first bytes, not its file name) and
`strip_metadata()` removes what a camera writes into it (location, device, time) before it is kept.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .library import tesseract_folder
from .normalize import BRAND_ALIASES, brand_key

MAX_IMAGE = 15_000_000  # bytes; the web page sends a JPEG of about 0.5 to 2 MB
MAX_PIXELS = 40_000_000  # a phone camera is 12 to 50 MP; the web page sends at most 2400 px on the long side
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}

# Makers of things with rating plates that have no adapter yet, so a plate from one still gets a brand.
# An adapter's own brand and aliases are added to these (see `brand_names`).
COMMON_BRANDS = [
    "A. O. Smith", "Amana", "Bosch", "Bradford White", "Broan", "Bryant", "Carrier", "Daikin", "Danby",
    "Dyson", "Electrolux", "Emerson", "Fisher & Paykel", "Frigidaire", "Fujitsu", "Generac", "Goodman", "Haier",
    "Hisense", "Insinkerator", "Kenmore", "Kohler", "Lennox", "Liebherr", "Midea", "Miele", "Navien", "Panasonic",
    "Payne", "Rheem", "Rinnai", "Ruud", "Sharp", "Speed Queen", "Sub-Zero", "Thermador", "Toshiba", "Trane",
    "Viking", "Weil-McLain", "York",
]  # fmt: skip

# Labels, as a regex over upper-case text. Order matters only for readability.
_MODEL_LABEL = (
    r"MODEL(?:E|O)?|MODÈLE|MOD|MDL|M\s*/\s*N|M\.\s*N\.?|CAT(?:ALOG)?\.?\s*(?:NO|#)|TYPE\s*/\s*MODEL"
)
_SERIAL_LABEL = r"SERIAL|SERIE|SÉRIE|SER|S\s*/\s*N|S\.\s*N\.?|SN"
# What may follow a label before its value: "NO.", "NUMBER", "#", a colon, and a repeated label in another
# language ("MODEL/MODÈLE/MODELO:").
_FILLER = r"(?:\s*(?:N[O0º°]\.?|NUMBER|NUM\.?|#|:|\.|-|=|/|\bDE\b))*\s*"
# A label ends at a non-letter, or runs straight into "NO" or "NUMBER" ("MODELNO", as OCR often reads it).
_NO_LETTER = r"(?!(?!N[O0º°]|NUMBER)[A-Z])"
_MODEL_RE = re.compile(rf"(?<![A-Z0-9])(?:{_MODEL_LABEL}){_NO_LETTER}{_FILLER}", re.I)
_SERIAL_RE = re.compile(rf"(?<![A-Z0-9])(?:{_SERIAL_LABEL}){_NO_LETTER}{_FILLER}", re.I)
_VALUE = re.compile(r"[A-Z0-9][A-Z0-9\-/.()]{1,30}", re.I)
# Words that sit where a value would, but aren't one.
_NOT_VALUES = {"NO", "NUMBER", "NUM", "MODEL", "MODELE", "MODELO", "MOD", "SERIAL", "SERIE", "SER", "TYPE"}
# Ratings that look like model numbers: 120V, 60HZ, 15A, 1500W, R-410A, 2.5KW, 115VAC, 300PSI.
_RATING = re.compile(
    r"^(?:\d+(?:[.,]\d+)?(?:V|VAC|VDC|HZ|A|AMPS?|W|KW|KWH|VA|PSI|PSIG|LBS?|KG|OZ|G|BTU|BTUH|MA|MM|CM|IN|F|C|RPM)"
    r"|R-?\d{2,3}[A-Z]?|\d{1,4}|IP\d{2}|UL|ETL|CSA|CE|FCC|USA|MADE|CHINA|MEXICO|KOREA)$",
    re.I,
)
# Not model numbers either, though they have letters and digits: a phone number (1-800-4-DEWALT), a speed or rate
# (0-20000/MIN, 3000RPM, 60/MIN) and a postcode with its state (MD21286).
_NOT_MODEL = re.compile(r"^1-?8\d\d-|/(?:MIN|SEC|S|H|HR)$|(?:RPM|OPM|SPM|BPM|IPM)$|^[A-Z]{2}\d{5}$", re.I)
# The usual shape of a model number: a few letters, then digits (DCS355, WM5700HVA, MSZ-FH12NA).
_MODEL_SHAPE = re.compile(r"^[A-Z]{1,5}-?[A-Z]{0,3}\d", re.I)


@dataclass
class Suggestion:
    """One reading of a field: the value, why it was picked, and where it came from (`ocr` or a model's name)."""

    value: str
    why: str
    source: str = "ocr"
    sure: bool = True  # False for a guess (a model-like word with no label in front of it)


@dataclass
class Reading:
    """What was read from a plate: suggestions for each field, best first, and the OCR text they came from."""

    brands: list[Suggestion] = field(default_factory=list)
    models: list[Suggestion] = field(default_factory=list)
    serials: list[Suggestion] = field(default_factory=list)
    text: str = ""

    def as_dict(self) -> dict:
        """The reading as plain data, for JSON."""
        return asdict(self)


def image_type(data: bytes) -> str | None:
    """The image's type from its first bytes: "image/jpeg", "image/png", or None for anything else."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """The image's (width, height) from its header, without decoding it, or None when it can't be read.

    For refusing an image too large to OCR before Tesseract decodes it: a small, highly compressed file can
    hold a picture of billions of pixels.
    """
    kind = image_type(data)
    if kind == "image/png" and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if kind != "image/jpeg":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker in (0x01, *range(0xD0, 0xD8)) or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        # SOF0 to SOF15 hold the size, except DHT (C4), JPG (C8) and DAC (CC)
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return int.from_bytes(data[i + 7 : i + 9], "big"), int.from_bytes(data[i + 5 : i + 7], "big")
        if marker == 0xDA:
            return None
        i += 2 + int.from_bytes(data[i + 2 : i + 4], "big")
    return None


def too_big(data: bytes) -> str:
    """Why an image is too large to OCR ("" when it isn't): more than MAX_PIXELS, or a size its header hides."""
    size = image_size(data)
    if size is None:
        return "can't read the photo's size"
    if size[0] * size[1] > MAX_PIXELS:
        return f"the photo is {size[0]}×{size[1]} pixels; at most {MAX_PIXELS // 1_000_000} megapixels"
    return ""


def strip_metadata(data: bytes) -> bytes:
    """The image without the metadata a camera or editor writes into it.

    JPEG: drops the APP1 (Exif, with location and orientation; XMP), APP12, APP13 (IPTC) and comment segments.
    PNG: drops the eXIf, tEXt, iTXt, zTXt and tIME chunks. Anything else is returned unchanged. The picture
    itself is not re-encoded. A file that doesn't parse is returned as it was rather than half-cut.
    """
    kind = image_type(data)
    try:
        if kind == "image/jpeg":
            return _strip_jpeg(data)
        if kind == "image/png":
            return _strip_png(data)
    except (IndexError, ValueError):
        pass
    return data


def _strip_jpeg(data: bytes) -> bytes:
    out = bytearray(data[:2])
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            raise ValueError("not a marker")
        marker = data[i + 1]
        if marker == 0xDA:  # start of scan: the rest is the picture
            out += data[i:]
            return bytes(out)
        if marker in (0x01, *range(0xD0, 0xD8)):  # markers without a length
            out += data[i : i + 2]
            i += 2
            continue
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        segment = data[i : i + 2 + length]
        if marker not in (0xE1, 0xEC, 0xED, 0xFE):
            out += segment
        i += 2 + length
    raise ValueError("no picture data")


def _strip_png(data: bytes) -> bytes:
    out = bytearray(data[:8])
    i = 8
    while i < len(data):
        length = int.from_bytes(data[i : i + 4], "big")
        kind = data[i + 4 : i + 8]
        chunk = data[i : i + 12 + length]
        if len(chunk) != 12 + length:
            raise ValueError("cut short")
        if kind not in (b"eXIf", b"tEXt", b"iTXt", b"zTXt", b"tIME"):
            out += chunk
        i += 12 + length
        if kind == b"IEND":
            return bytes(out)
    raise ValueError("no IEND")


def tesseract_command() -> str | None:
    """The `tesseract` program, from `library.tesseract_folder()`, or None when it isn't installed."""
    folder = tesseract_folder()
    if folder is None:
        return None
    exe = folder / ("tesseract.exe" if os.name == "nt" else "tesseract")
    return str(exe) if exe.exists() else shutil.which("tesseract")


def ocr_available() -> bool:
    """Whether photos can be read: Tesseract is installed (OCRmyPDF isn't needed for photos)."""
    return tesseract_command() is not None


def grey_for_ocr(data: bytes) -> tuple[bytes, str]:
    """(image bytes, suffix) for Tesseract: the photo upright, in grey, its contrast stretched, as PNG.

    Tesseract reads coloured plates badly: on a photo of a yellow DeWalt label it found no word at all in colour,
    and the model, brand and serial label once the photo was grey. Needs Pillow (the `web` extra); without it the
    photo goes to Tesseract as it is.
    """
    try:
        from io import BytesIO

        from PIL import Image, ImageOps
    except ImportError:
        return data, IMAGE_TYPES.get(image_type(data) or "", ".img")
    with Image.open(BytesIO(data)) as im:
        grey = ImageOps.autocontrast(ImageOps.grayscale(ImageOps.exif_transpose(im)), cutoff=1)
        out = BytesIO()
        grey.save(out, format="PNG")
    return out.getvalue(), ".png"


def ocr_image(data: bytes, timeout: float = 120) -> str:
    """The text Tesseract reads in a photo (JPEG or PNG bytes), or "" when it isn't installed.

    The photo is greyed first (`grey_for_ocr`). Tesseract runs twice, as a block of text (page segmentation mode 6,
    which keeps a label and its value on one line) and as sparse text (mode 11, which finds words scattered over a
    plate), and both are returned, one after the other.
    Raises RuntimeError when Tesseract fails, or the image is too large (`too_big`).
    """
    command = tesseract_command()
    if not command:
        return ""
    if refusal := too_big(data):
        raise RuntimeError(refusal)
    data, suffix = grey_for_ocr(data)
    with tempfile.TemporaryDirectory(prefix="mb-plate-") as tmp:
        path = Path(tmp) / f"plate{suffix}"
        path.write_bytes(data)
        texts = []
        for psm in ("6", "11"):
            r = subprocess.run(
                [command, str(path), "stdout", "--psm", psm, "-l", "eng"],
                capture_output=True,
                timeout=timeout,
            )
            if r.returncode != 0:
                err = r.stderr.decode("utf-8", errors="replace").strip().splitlines()
                raise RuntimeError(f"Tesseract failed: {err[-1] if err else r.returncode}")
            texts.append(r.stdout.decode("utf-8", errors="replace"))
    return "\n".join(texts)


def brand_names(names: Iterable[str]) -> dict[str, str]:
    """Brand names to look for on a plate: `brand_key(name)` -> the name to suggest.

    `names` are the adapters' brands and aliases (`Registry`); the shared aliases in normalize.py and
    `COMMON_BRANDS` are added. The first spelling of a key wins.
    """
    out: dict[str, str] = {}
    for name in [*names, *COMMON_BRANDS, *BRAND_ALIASES.values()]:
        out.setdefault(brand_key(name), name)
    for alias, brand in BRAND_ALIASES.items():
        out.setdefault(brand_key(alias), brand)
    return {k: v for k, v in out.items() if k}


def _clean_value(v: str) -> str:
    return v.strip(".-/()").upper()


def _is_value(v: str) -> bool:
    v = _clean_value(v)
    return (
        len(v) >= 3
        and v not in _NOT_VALUES
        and any(ch.isdigit() for ch in v)
        and not _RATING.match(v)
        and not re.fullmatch(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", v)  # a date
    )


def _labelled(lines: list[str], label: re.Pattern) -> list[tuple[str, str]]:
    """(value, label as written) for each label in the text whose value follows it on its line, or on the next."""
    found = []
    for n, line in enumerate(lines):
        for m in label.finditer(line):
            rest = line[m.end() :]
            # A value is the first word after the label; one that isn't a value ends the search on this line.
            words = _VALUE.findall(rest[:40])
            value = next((w for w in words[:3] if _is_value(w)), None)
            if value is None and not rest.strip() and n + 1 < len(lines):
                nxt = _VALUE.findall(lines[n + 1])
                value = nxt[0] if nxt and _is_value(nxt[0]) else None
            if value:
                found.append((_clean_value(value), m.group(0).strip(" :.#-=/")))
    return found


def _dedupe(items: list[Suggestion]) -> list[Suggestion]:
    seen: dict[str, Suggestion] = {}
    for s in items:
        seen.setdefault(s.value, s)
    return list(seen.values())


def read_plate(text: str, names: dict[str, str]) -> Reading:
    """Suggest a brand, model and serial from a plate's OCR text.

    `names` is `brand_names()`. Models and serials come from their labels first; when no label is found, words
    that look like model numbers (letters and digits, 5 to 20 long, not a rating such as 120V, a phone number or
    a speed) are offered as guesses (`sure` False), letters-then-digits shapes such as DCS355 first, then the
    most often read. Brands are matched as whole words, most often seen first; a name inside a longer one
    that also matched ("Mitsubishi" in "Mitsubishi Electric") is dropped. Two-letter names (LG, GE) must be
    written in capitals on the plate.
    """
    lines = [ln.strip() for ln in text.upper().splitlines() if ln.strip()]
    serials = [Suggestion(v, f"after “{lab}”") for v, lab in _labelled(lines, _SERIAL_RE)]
    serial_values = {s.value for s in serials}
    models = [
        Suggestion(v, f"after “{lab}”") for v, lab in _labelled(lines, _MODEL_RE) if v not in serial_values
    ]
    if not models:
        counts: dict[str, int] = {}
        for word in _VALUE.findall(text.upper()):
            w = _clean_value(word)
            letters, digits = sum(c.isalpha() for c in w), sum(c.isdigit() for c in w)
            if (
                5 <= len(w) <= 20
                and letters >= 2
                and digits >= 2
                and _is_value(w)
                and not _NOT_MODEL.search(w)
                and w not in serial_values
            ):
                counts[w] = counts.get(w, 0) + 1
        best = sorted(counts, key=lambda w: (not _MODEL_SHAPE.match(w), -counts[w], -len(w)))[:3]
        models = [Suggestion(w, "looks like a model number (no label found)", sure=False) for w in best]
    return Reading(
        brands=_brands(text, names), models=_dedupe(models), serials=_dedupe(serials), text=text.strip()
    )


def _brands(text: str, names: dict[str, str]) -> list[Suggestion]:
    hay = " " + brand_key(text) + " "
    capitals = set(re.findall(r"\b[A-Z]{2}\b", text))
    hits: dict[str, int] = {}
    for key in names:
        if len(key) <= 2:
            if key.upper() in capitals:
                hits[key] = len(re.findall(rf"\b{key.upper()}\b", text))
            continue
        n = hay.count(f" {key} ")
        if n:
            hits[key] = n
    keys = [k for k in hits if not any(k != o and f" {k} " in f" {o} " for o in hits)]
    keys.sort(key=lambda k: (-hits[k], hay.find(f" {k} "), -len(k)))
    out = []
    for k in keys[:3]:
        why = "named on the plate" + (f" {hits[k]} times" if hits[k] > 1 else "")
        out.append(Suggestion(names[k], why))
    return _dedupe(out)
