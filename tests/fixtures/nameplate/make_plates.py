"""Draw the made-up rating plates the nameplate tests read. Needs Pillow (not a dependency of Manual Beagle).

    python tests/fixtures/nameplate/make_plates.py

The plates are invented: real brand names, but serial numbers that belong to no one and no photo of anyone's home.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent


def plate(lines: list[tuple[str, int]], size=(900, 520), bg=(236, 236, 230), ink=(30, 30, 32)) -> Image.Image:
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    d.rectangle([12, 12, size[0] - 13, size[1] - 13], outline=ink, width=3)
    y = 34
    for text, pt in lines:
        font = ImageFont.load_default(size=pt)
        d.text((40, y), text, font=font, fill=ink)
        y += int(pt * 1.45)
    return im


lg = plate(
    [
        ("LG", 64),
        ("WASHING MACHINE", 30),
        ("MODEL NO.: WM3400CW", 36),
        ("SERIAL NO.: 012KWZZ00000", 36),
        ("120V~ 60Hz 10A", 30),
        ("MADE IN USA", 26),
    ]
)
lg.save(HERE / "plate_lg.png", optimize=True)

wp = plate(
    [
        ("Whirlpool", 60),
        ("REFRIGERATOR / RÉFRIGÉRATEUR", 26),
        ("MOD/MOD  WRF555SDFZ00", 36),
        ("SER/SER  TX0000000", 36),
        ("115V 60Hz 6.5A   R-600a 3.2 oz", 28),
    ],
    size=(900, 470),
)
wp = wp.rotate(2, resample=Image.BICUBIC, fillcolor=(236, 236, 230)).filter(ImageFilter.GaussianBlur(0.6))
wp.save(HERE / "plate_whirlpool.jpg", quality=85, optimize=True)
print("ok")
