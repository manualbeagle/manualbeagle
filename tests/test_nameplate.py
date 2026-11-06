"""Nameplate scanning, offline: the rules-only reader, photo checks, the vision plugins and the web API.

The plates in fixtures/nameplate/ are made up (make_plates.py draws them). Reading them with real Tesseract is
skipped when it isn't installed; every other test feeds OCR text in directly.
"""

import json
import struct
import zlib
from pathlib import Path

import pytest

from manualbeagle import nameplate, vision
from manualbeagle.adapters import Registry
from manualbeagle.config import AIConfig, Config
from manualbeagle.http import Response
from manualbeagle.library import Library
from manualbeagle.nameplate import brand_names, image_type, read_plate, strip_metadata

FIXTURES = Path(__file__).parent / "fixtures" / "nameplate"
NAMES = brand_names(n for a in Registry() for n in a.names())


def values(found):
    return [s.value for s in found]


PLATE_TEXT = "LG\nWASHING MACHINE\nMODEL NO.: WM3400CW\nSERIAL NO.: 012KWZZ00000\n120V~ 60Hz 10A"


# Reading the text


def test_labelled_model_and_serial():
    r = read_plate(
        "LG Electronics\nWASHING MACHINE\nMODEL NO. WM3400CW\nSERIAL NO.: 012KWZZ00000\n120V~ 60Hz 10A", NAMES
    )
    assert values(r.brands) == ["LG Electronics"]
    assert values(r.models) == ["WM3400CW"] and r.models[0].sure and "MODEL NO" in r.models[0].why
    assert values(r.serials) == ["012KWZZ00000"]


@pytest.mark.parametrize(
    "text",
    [
        "M/N: MSZ-FH12NA",
        "MODELNO:MSZ-FH12NA",  # OCR often loses the space
        "MOD/MOD  MSZ-FH12NA",
        "MODEL/MODÈLE/MODELO: MSZ-FH12NA",
        "Model Number\nMSZ-FH12NA",  # the value on the next line, as on a plate laid out in a table
        "MDL # MSZ-FH12NA",
    ],
)
def test_model_label_spellings(text):
    assert values(read_plate(text, NAMES).models) == ["MSZ-FH12NA"]


def test_ratings_and_serials_are_not_models():
    r = read_plate("MODEL 120V\nS/N AB1234567\nR-410A 60HZ 1500W", NAMES)
    assert "120V" not in values(r.models) and "AB1234567" not in values(r.models)
    assert values(r.serials) == ["AB1234567"]


def test_unlabelled_model_is_a_guess():
    r = read_plate("GE Appliances\nGTW485ASJ4WS\n120V 60HZ 15A", NAMES)
    assert values(r.models) == ["GTW485ASJ4WS"] and not r.models[0].sure
    assert values(r.brands) == ["GE Appliances"]


def test_phone_numbers_and_speeds_are_not_model_guesses():
    # As OCR reads a power tool's plate, which names its model without a label.
    text = (
        "DCS355 CORDLESS OSCILLATING MULTI-TOOL\nTYPE 2 no 0-20000/min (OPM)\n"
        "DEWALT INDUSTRIAL TOOL CO., TOWSON, MD21286 U.S.A.\nFOR SERVICE INFORMATION, CALL 1-800-4-DEWALT"
    )
    r = read_plate(text, NAMES)
    assert values(r.models) == ["DCS355"] and not r.models[0].sure
    assert values(r.brands)[0] == "DeWalt"


def test_model_shaped_guesses_come_first():
    r = read_plate("12345-6789AB\nWM5700HVA", NAMES)
    assert values(r.models)[0] == "WM5700HVA"


def test_brands_whole_words_longest_and_capitals():
    assert values(read_plate("MITSUBISHI ELECTRIC\nM/N MSZ-FH12NA", NAMES).brands) == ["Mitsubishi Electric"]
    # "lg" and "ge" inside other words, or in lower case, aren't brands
    assert read_plate("voltage page bulge\nlg ge", NAMES).brands == []
    assert values(read_plate("MAYTAG\nMOD MVW6230HW0", NAMES).brands) == ["Maytag"]
    assert values(read_plate("Rheem\nMODEL XE50T10H45U0", NAMES).brands) == [
        "Rheem"
    ]  # no adapter: a common maker


def test_nothing_read():
    r = read_plate("", NAMES)
    assert r.brands == r.models == r.serials == [] and r.text == ""


# The photo itself


def test_image_type_by_content():
    assert image_type((FIXTURES / "plate_lg.png").read_bytes()) == "image/png"
    assert image_type((FIXTURES / "plate_whirlpool.jpg").read_bytes()) == "image/jpeg"
    assert image_type(b"GIF89a....") is None and image_type(b"") is None


def test_strip_jpeg_metadata():
    jpeg = (FIXTURES / "plate_whirlpool.jpg").read_bytes()
    exif = b"Exif\x00\x00" + b"GPS 40.0N 75.0W" * 4
    tagged = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:]
    out = strip_metadata(tagged)
    assert b"GPS 40.0N" not in out and out[:2] == b"\xff\xd8" and out.endswith(jpeg[-64:])
    assert strip_metadata(out) == out


def test_strip_png_metadata():
    png = (FIXTURES / "plate_lg.png").read_bytes()
    data = b"Comment\x00taken at a friend's house"
    chunk = struct.pack(">I", len(data)) + b"tEXt" + data + struct.pack(">I", zlib.crc32(b"tEXt" + data))
    tagged = png[:33] + chunk + png[33:]  # after the 8-byte signature and the 25-byte IHDR chunk
    out = strip_metadata(tagged)
    assert b"friend's house" not in out and out == strip_metadata(png)


def test_strip_leaves_a_broken_file_alone():
    assert strip_metadata(b"\xff\xd8\xff\xe1\x00") == b"\xff\xd8\xff\xe1\x00"


# Real OCR on the made-up plates (needs Tesseract)


ocr = pytest.mark.skipif(not nameplate.ocr_available(), reason="Tesseract isn't installed")


def test_photos_are_greyed_for_ocr():
    pytest.importorskip("PIL")
    from io import BytesIO

    from PIL import Image

    data, suffix = nameplate.grey_for_ocr((FIXTURES / "plate_whirlpool.jpg").read_bytes())
    assert suffix == ".png" and image_type(data) == "image/png"
    with Image.open(BytesIO(data)) as im:
        assert im.mode == "L"


@ocr
def test_ocr_reads_a_png_plate():
    r = read_plate(nameplate.ocr_image((FIXTURES / "plate_lg.png").read_bytes()), NAMES)
    assert values(r.brands)[0] == "LG" and values(r.models)[0] == "WM3400CW"


@ocr
def test_ocr_reads_a_tilted_jpeg_plate():
    r = read_plate(nameplate.ocr_image((FIXTURES / "plate_whirlpool.jpg").read_bytes()), NAMES)
    assert values(r.brands)[0] == "Whirlpool"
    assert any(m.replace("O", "0") == "WRF555SDFZ00" for m in values(r.models))


# Vision plugins


class FakeAPI:
    """Answers one POST with a canned JSON body, and remembers what was sent."""

    def __init__(self, body: dict, status: int = 200) -> None:
        self.body, self.status, self.sent = body, status, []

    def post(self, url, json_body=None, headers=None, check_challenge=True):
        self.sent.append({"url": url, "json": json_body, "headers": headers or {}})
        return Response(
            self.status, url, {"content-type": "application/json"}, json.dumps(self.body).encode()
        )


def test_model_numbers_lose_added_words():
    assert vision.code_only("DCS355 CORDLESS OSCILLATING MULTI-TOOL") == "DCS355"
    assert vision.code_only("Model: WM3400CW") == "WM3400CW"
    assert vision.code_only("MSZ-FH12NA") == "MSZ-FH12NA"


def test_parse_answer():
    assert vision.parse_answer('Sure: {"brand": "LG", "model": "WM3400CW", "serial": ""}') == {
        "brand": "LG",
        "model": "WM3400CW",
        "serial": "",
    }
    with pytest.raises(vision.VisionError):
        vision.parse_answer("I can't read it")


def test_no_vision_by_default_and_unknown_provider():
    assert vision.make_vision(AIConfig(), None) is None
    with pytest.raises(vision.VisionError):
        vision.make_vision(AIConfig(provider="something"), None)


def test_ollama_vision():
    api = FakeAPI({"message": {"content": '{"brand": "Whirlpool", "model": "WRF555SDFZ00", "serial": "X1"}'}})
    v = vision.make_vision(AIConfig(provider="ollama", url="http://192.168.1.50:11434"), api)
    found = v.read(b"\xff\xd8\xffjpeg", "image/jpeg")
    assert values(found["models"]) == ["WRF555SDFZ00"] and found["models"][0].source == "ollama gemma3"
    sent = api.sent[0]
    assert sent["url"] == "http://192.168.1.50:11434/api/chat" and sent["json"]["format"] == "json"
    assert sent["json"]["messages"][0]["images"] == ["/9j/anBlZw=="] and not v.leaves_network


def test_anthropic_vision(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    api = FakeAPI(
        {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": '{"brand":"LG","model":"WM3400CW"}'}],
        }
    )
    v = vision.make_vision(AIConfig(provider="anthropic"), api)
    found = v.read(b"\x89PNG\r\n\x1a\npng", "image/png")
    assert values(found["brands"]) == ["LG"] and found["serials"] == [] and v.leaves_network
    sent = api.sent[0]
    assert sent["headers"]["x-api-key"] == "test-key" and sent["headers"]["anthropic-version"] == "2023-06-01"
    image = sent["json"]["messages"][0]["content"][0]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/png"
    assert sent["json"]["model"] == "claude-opus-5-5"


def test_anthropic_vision_errors(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(vision.VisionError, match="no API key"):
        vision.make_vision(AIConfig(provider="anthropic"), FakeAPI({})).read(b"x", "image/jpeg")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    refused = FakeAPI({"stop_reason": "refusal", "content": []})
    with pytest.raises(vision.VisionError, match="declined"):
        vision.make_vision(AIConfig(provider="anthropic"), refused).read(b"x", "image/jpeg")
    bad = FakeAPI({"error": {"message": "invalid x-api-key"}}, status=401)
    with pytest.raises(vision.VisionError, match="invalid x-api-key"):
        vision.make_vision(AIConfig(provider="anthropic"), bad).read(b"x", "image/jpeg")


def test_only_the_vision_client_may_reach_ollama():
    """A lookup's URLs come from web sites, so only the vision model's client may reach a private Ollama."""
    cfg = Config()
    assert cfg.ollama_url() == ""
    cfg.ai = AIConfig(provider="ollama", url="http://192.168.1.50:11434")
    assert cfg.ollama_url() == "http://192.168.1.50:11434"
    assert cfg.ollama_url() not in cfg.private_hosts()
    origin = ("http", "192.168.1.50", 11434)
    assert origin not in cfg.client().private_ok
    assert origin in cfg.client(extra_private=[cfg.ollama_url()]).private_ok


def test_image_size_and_pixel_limit():
    assert nameplate.image_size((FIXTURES / "plate_lg.png").read_bytes()) == (900, 520)
    assert nameplate.image_size((FIXTURES / "plate_whirlpool.jpg").read_bytes()) == (900, 470)
    assert nameplate.too_big((FIXTURES / "plate_lg.png").read_bytes()) == ""
    png = (FIXTURES / "plate_lg.png").read_bytes()
    bomb = png[:16] + (60000).to_bytes(4, "big") + (60000).to_bytes(4, "big") + png[24:]  # 3.6 gigapixels
    assert "megapixels" in nameplate.too_big(bomb)
    assert nameplate.too_big(bytes([0xFF, 0xD8, 0xFF])) == "can't read the photo's size"


# The library keeps photos


def test_library_photos(tmp_path):
    lib = Library(tmp_path / "library", tmp_path / "db.sqlite")
    data = (FIXTURES / "plate_lg.png").read_bytes()
    row = lib.add_photo(data, "image/png", "MODEL NO. WM3400CW")
    assert Path(row["path"]).read_bytes() == data and row["ocr_text"] == "MODEL NO. WM3400CW"
    assert lib.label_photo(row["sha256"], "LG", "WM3400CW", "012KWZZ00000")
    again = lib.add_photo(data, "image/png", "")  # the same photo again keeps its label
    assert again["sha256"] == row["sha256"] and again["model"] == "WM3400CW"
    assert [p["model"] for p in lib.photos()] == ["WM3400CW"]
    assert lib.delete_photo(row["sha256"]) and not Path(row["path"]).exists()
    assert (
        lib.photos() == [] and not lib.delete_photo(row["sha256"]) and not lib.label_photo("0" * 64, "a", "b")
    )
    lib.close()


# The command line


def test_cli_nameplate(capsys, monkeypatch, tmp_path):
    from manualbeagle import cli

    monkeypatch.setattr(nameplate, "ocr_available", lambda: True)
    monkeypatch.setattr(nameplate, "ocr_image", lambda data: PLATE_TEXT)
    monkeypatch.setenv("MANUALBEAGLE_CONFIG", str(tmp_path / "none.toml"))
    assert cli.main(["nameplate", str(FIXTURES / "plate_lg.png")]) == 0
    out = capsys.readouterr().out
    assert "✓ brand: LG" in out and "✓ model: WM3400CW" in out
    assert cli.main(["nameplate", str(FIXTURES / "make_plates.py")]) == 2
    assert cli.main(["nameplate", str(FIXTURES / "plate_lg.png"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out.split("\n", 1)[1])["models"][0]["value"] == "WM3400CW"
