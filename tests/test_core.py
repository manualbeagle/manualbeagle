from pathlib import Path

from manualbeagle.adapters import Registry
from manualbeagle.adapters.base import AdapterSpec, Context, guess_kind
from manualbeagle.adapters.yaml_adapter import YamlAdapter
from manualbeagle.http import Cassette, PoliteClient, Response
from manualbeagle.models import Candidate, DocKind, Query, strip_tracking
from manualbeagle.normalize import generic_variants, normalise, split_year
from manualbeagle.verify import is_staging, rank, score, url_locale


def test_variants_strip_market_suffixes():
    v = generic_variants("LRFXS2503S/00")
    assert v[0] == "LRFXS2503S/00"
    assert "LRFXS2503S" in v
    assert generic_variants("LRFXS2503S.ASTEUS")[1] == "LRFXS2503S"
    assert "MSZFH12NA" in generic_variants("msz-fh12na")


def test_adapter_suffix_rules_come_first():
    v = generic_variants("MSZ-FH12NA2", [r"\d$"])
    assert v[:2] == ["MSZ-FH12NA2", "MSZ-FH12NA"]


def test_brand_alias():
    q = normalise(Query(brand="Honeywell Home", model="RCHT9610WF"))
    assert q.canonical_brand == "Resideo"
    assert normalise(Query(brand="Mitsubishi", model="x")).canonical_brand == "Mitsubishi Electric"


def test_vehicle_year_split():
    assert split_year("2021 Sienna") == ("Sienna", 2021)
    q = normalise(Query(brand="Toyota", model="Sienna 2022"), vehicle=True)
    assert (q.model, q.year) == ("Sienna", 2022)
    # Appliance models keep digits that look like years
    assert normalise(Query(brand="LG", model="LW 2019")).model == "LW 2019"


def test_guess_kind():
    assert guess_kind("Owner's Manual") == DocKind.owners_manual
    assert guess_kind("Installation Instructions") == DocKind.install_guide
    assert guess_kind("Quick Start Guide") == DocKind.quick_start
    assert guess_kind("Repair Parts List") == DocKind.parts_list
    assert guess_kind("Specification Sheet") == DocKind.spec_sheet
    assert guess_kind("Special offer") is None


def test_maker_pdf_beats_mirror_and_retailer():
    q = normalise(Query(brand="LG", model="LW8016ER"))
    cands = [
        Candidate(url="https://www.manualslib.com/manual/1/lg-lw8016er.html", title="LG LW8016ER manual"),
        Candidate(url="https://images.thdstatic.com/x/LW8016ER.pdf", title="Owner's manual LW8016ER"),
        Candidate(url="https://gscs.lge.com/x/MFL1.pdf", title="Owner's Manual", source="adapter:lg"),
    ]
    ranked = rank(cands, q, ["lge.com", "lg.com"])
    assert ranked[0].host == "gscs.lge.com"
    assert ranked[-1].link_only  # the mirror is link-only


def test_non_english_penalty():
    q = normalise(Query(brand="LG", model="LW8016ER"))
    en = score(Candidate(url="https://a/x.pdf", title="LW8016ER Owner's Manual"), q, [])
    es = score(Candidate(url="https://a/y.pdf", title="LW8016ER Manual del propietario (Spanish)"), q, [])
    assert en.score > es.score


def _fake_http(tmp_path: Path, entries: dict[str, str], ctype="application/json") -> PoliteClient:
    c = Cassette(tmp_path / "c.json", mode="record")
    for key, body in entries.items():
        c.put(key, Response(200, key.split(" ", 1)[1], {"content-type": ctype}, body.encode()))
    c.save()
    return PoliteClient(cassette=Cassette(tmp_path / "c.json", mode="replay"))


def test_yaml_adapter_json(tmp_path):
    spec = AdapterSpec.model_validate(
        {
            "id": "ex",
            "brand": "Example",
            "domains": ["example.com"],
            "search": [
                {
                    "url": "https://example.com/api?m={model}",
                    "results": "$.docs[*]",
                    "fields": {"url": "$.pdf", "title": "$.name", "kind": "$.cat"},
                }
            ],
            "kinds": {"Owner's Manual": "owners_manual"},
        }
    )
    http = _fake_http(
        tmp_path,
        {
            "GET https://example.com/api?m=EX-1": '{"docs": [{"pdf": "/d/a.pdf", "name": "EX-1 guide", "cat": "Owner\'s Manual"}]}',
        },
    )
    q = normalise(Query(brand="Example", model="EX-1"))
    found = list(YamlAdapter(spec).find(q, Context(http=http)))
    assert found[0].url == "https://example.com/d/a.pdf"
    assert found[0].kind == DocKind.owners_manual


def test_yaml_adapter_html(tmp_path):
    spec = AdapterSpec.model_validate(
        {
            "id": "ex",
            "brand": "Example",
            "search": [
                {
                    "url": "https://example.com/s/{model_path}",
                    "format": "html",
                    "results": "li.doc",
                    "fields": {"url": "a@href", "title": "a"},
                    "keep_url": r"\.pdf$",
                }
            ],
        }
    )
    html = '<ul><li class="doc"><a href="/m/x.pdf">Installation Guide</a></li><li class="doc"><a href="/p">no</a></li></ul>'
    http = _fake_http(tmp_path, {"GET https://example.com/s/EX-2": html}, "text/html")
    found = list(YamlAdapter(spec).find(normalise(Query(brand="Example", model="EX-2")), Context(http=http)))
    assert [c.url for c in found] == ["https://example.com/m/x.pdf"]
    assert found[0].kind == DocKind.install_guide


def test_registry_loads_builtins():
    reg = Registry()
    for a in reg:
        assert a.spec.brand
        assert a.spec.status in ("works", "partial", "blocked", "link", "untested")


def test_refuses_private_and_odd_schemes():
    import pytest

    from manualbeagle.http import UnsafeURL

    with PoliteClient(respect_robots=False) as c:
        for url in (
            "http://127.0.0.1/x",
            "http://10.0.0.1/x",
            "http://169.254.169.254/latest",
            "http://[::1]/",
        ):
            with pytest.raises(UnsafeURL):
                c.get(url)


def test_refuse_hook_runs_on_every_hop():
    import httpx
    import pytest

    from manualbeagle.http import UnsafeURL

    c = PoliteClient(refuse=lambda u: "a mirror" if "mirror.example" in u else "")
    with pytest.raises(UnsafeURL, match="a mirror"):
        c._check_target(httpx.Request("GET", "https://mirror.example/m.pdf"))
    c.close()


def test_private_ok_host_is_allowed_through_the_check():
    import httpx
    import pytest

    from manualbeagle.http import UnsafeURL

    c = PoliteClient(private_ok=["http://127.0.0.1:8080"])
    c._check_target(httpx.Request("GET", "http://127.0.0.1:8080/search"))  # no exception
    with pytest.raises(UnsafeURL):
        c._check_target(httpx.Request("GET", "http://127.0.0.1:9000/admin"))  # another port: refused
    c.close()


def test_wildcard_family_names():
    from manualbeagle.verify import model_in, wildcard_match

    assert wildcard_match("WM6700H*A / WM5700H*A", "WM5700HVA")
    assert wildcard_match("MSZ-FH**NA", "MSZ-FH12NA")
    assert not wildcard_match("WM5700H*A", "DLEX5500V")
    assert model_in("ENGLISH WM6700H*A / WM5700H*A", ["WM5700HVA"]) == 2
    # Hostile text stays fast
    assert not wildcard_match(("A*" * 5000) + "B", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAC")


def test_model_must_stand_on_its_own():
    from manualbeagle.verify import model_in

    assert model_in("CL900_Instructions_web.pdf", ["CL900"]) == 3
    assert model_in("CL-900 manual", ["CL900"]) == 3
    assert model_in("CL900E_Instructions.pdf", ["CL900"]) == 1  # a sibling model: weak
    assert model_in("LG WM5700HVA.ASSEVUS", ["WM5700HVA"]) == 3
    assert model_in("nothing here", ["WM5700HVA"]) == 0


def test_bot_walls_and_ordinary_error_pages():
    from manualbeagle.http import _looks_like_challenge

    script = b'<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
    assert not _looks_like_challenge(Response(404, "https://x.example/", {}, b"<h1>Not found</h1>" + script))
    assert _looks_like_challenge(Response(403, "https://x.example/", {}, script))
    assert _looks_like_challenge(
        Response(200, "https://x.example/", {}, b"<title>Human Verification</title>")
    )
    assert not _looks_like_challenge(Response(403, "https://x.example/", {}, b"%PDF-1.7 Access Denied"))


def test_candidate_key_keeps_meaningful_query():
    a = Candidate(url="https://drive.google.com/uc?export=download&id=AAA", title="a")
    b = Candidate(url="https://drive.google.com/uc?export=download&id=BBB", title="b")
    assert a.key() != b.key()
    c = Candidate(url="https://x.example/m.pdf?utm_source=n&v=123", title="c")
    assert c.key() == Candidate(url="https://X.example/m.pdf", title="d").key()


def test_candidates_drop_tracking_parameters():
    c = Candidate(
        url="https://www.samsung.com/us/support/model/WMN4277ST/ZA/?msockid=09c3&utm_source=x&page=2",
        page_url="https://x.example/a?srsltid=abc",
    )
    assert c.url == "https://www.samsung.com/us/support/model/WMN4277ST/ZA/?page=2"
    assert c.page_url == "https://x.example/a"
    assert strip_tracking("https://x.example/uc?id=A&v=1") == "https://x.example/uc?id=A&v=1"


def test_staging_hosts_and_url_locales():
    assert is_staging("support.dev1.lutron.com") and is_staging("staging.example.com")
    assert not is_staging("support.lutron.com") and not is_staging("devtools.example.com")
    assert not is_staging("dev.example")  # the domain itself, not a label below it
    assert url_locale("https://support.lutron.com/us/es/instructions/PD15OUT") == ("es", "us")
    assert url_locale("https://www.samsung.com/hk_en/support/model/X/") == ("en", "hk")
    assert url_locale("https://x.example/en-gb/manuals/a.pdf") == ("en", "gb")
    assert url_locale("https://support.usa.canon.com/kb/s/article/ART177306") == (None, None)
    assert url_locale("https://www.samsung.com/bd/support/model/WMN4277ST/XL/") == (None, "bd")
    assert url_locale("https://x.example/tv/me/manual") == (None, None)  # words, not Tuvalu or Montenegro


def test_the_users_language_and_country_and_a_live_host_rank_first():
    q = normalise(Query(brand="Lutron", model="PD-15OUT"))
    title = "Lutron PD-15OUT instructions"
    en = Candidate(
        url="https://support.lutron.com/us/en/instructions/PD15OUT", title=title, source="search:s"
    )
    es = Candidate(
        url="https://support.lutron.com/us/es/instructions/PD15OUT", title=title, source="search:s"
    )
    dev = Candidate(
        url="https://support.dev1.lutron.com/us/en/instructions/PD15OUT", title=title, source="search:s"
    )
    hk = Candidate(
        url="https://support.lutron.com/hk_en/instructions/PD15OUT", title=title, source="search:s"
    )
    ranked = rank([es, dev, hk, en], q, ["lutron.com"])
    assert [c.url for c in ranked][:2] == [en.url, hk.url]
    assert "URL names language es -15" in es.reasons
    assert "URL names country hk -8" in hk.reasons
    assert ranked[-1] is dev and "a staging or test server -50" in dev.reasons


def test_an_alias_that_repeats_the_brand_lists_the_adapter_once():
    assert [a.id for a in Registry().for_brand("Levoit")] == ["levoit"]


def test_stripped_variants_get_hyphen_and_compact_forms():
    v = generic_variants("Litter-Robot 5 Pro", [r"\s*PRO$"])
    assert v[0] == "LITTER-ROBOT 5 PRO" and "LITTER-ROBOT 5" in v
    assert "LITTER-ROBOT-5" in v and "LITTERROBOT5" in v


def test_model_re_keeps_the_model_as_a_whole_name():
    import re

    from manualbeagle.adapters.yaml_adapter import template_vars

    q = normalise(Query(brand="eufy", model="RoboVac L35 Hybrid"))
    pat = r"^(.*[^A-Za-z0-9])?" + template_vars(q, "L35 HYBRID")["model_re"] + r"(?=[\s]*([_/(]|manual|$))"
    assert re.search(pat, "RoboVac L35 Hybrid_EN_Manual", re.I)
    assert re.search(pat, "RoboVac L35-Hybrid manual", re.I)
    assert not re.search(pat, "RoboVac L35 Hybrid+_EN_Manual", re.I)
    assert not re.search(pat, "RoboVac XL35 Hybrid_EN_Manual", re.I)
