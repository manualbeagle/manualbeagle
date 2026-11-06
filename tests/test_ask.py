"""Ask your manuals, offline: a library of generated PDFs, and a fake model that answers as it's told.

The live test asks a real Ollama; it is marked `live` and needs MANUALBEAGLE_TEST_OLLAMA (its address) and,
optionally, MANUALBEAGLE_TEST_OLLAMA_MODEL.
"""

import asyncio
import os

import pytest
from test_library import FakeHTTP, make_pdf
from test_nameplate import FakeAPI

from manualbeagle import ask, cli
from manualbeagle.config import AIConfig, Config
from manualbeagle.library import Library
from manualbeagle.models import Candidate, DocKind, Query
from manualbeagle.normalize import normalise

DRYER = [
    "OWNER'S MANUAL DLEX5500V electric dryer",
    "Cleaning the lint filter: pull the lint filter straight up and roll the lint off with your fingers",
    "Warranty: one year parts and labour",
]
ROBOT = [
    "Roborock S8 user manual",
    "Reset WiFi: press and hold both buttons until you hear Resetting WiFi",
    "Clean the main brush every week and the filter every two weeks",
]


def add(lib: Library, pages: list[str], brand: str, model: str, title: str) -> str:
    c = Candidate(url=f"https://example.com/{model}.pdf", title=title, kind=DocKind.owners_manual)
    return lib.fetch(c, normalise(Query(brand=brand, model=model)), FakeHTTP(make_pdf(pages))).sha256


@pytest.fixture
def cfg(tmp_path):
    return Config(home=tmp_path)


@pytest.fixture
def lib(cfg):
    lib = Library(cfg.library, cfg.db_path)
    lib.dryer = add(lib, DRYER, "LG", "DLEX5500V", "Owner's Manual")
    lib.robot = add(lib, ROBOT, "Roborock", "S8", "User Manual")
    yield lib
    lib.close()


class FakeModel(ask.Model):
    """Answers every question with `reply` (or raises `error`), and keeps the prompts it was sent."""

    provider = "ollama"

    def __init__(self, reply: str = "", error: str = "") -> None:
        super().__init__(AIConfig(provider="ollama", model="fake"), None)
        self.reply, self.error, self.sent = reply, error, []

    def complete(self, system: str, user: str) -> str:
        self.sent.append((system, user))
        if self.error:
            raise ask.AskError(self.error)
        return self.reply


# Retrieval


def test_keywords_leave_out_little_words():
    assert ask.keywords("How do I clean the lint filter?") == ["clean", "lint", "filter"]
    assert ask.keywords("What does error E5 mean? E5!") == ["error", "e5", "mean"]
    assert ask.fts_query(["door", 'say "hi"']) == '"door" OR "say ""hi"""'


def test_the_page_with_the_most_words_comes_first(lib):
    sources, terms, scope = ask.retrieve(lib, "how do I clean the lint filter?")
    assert terms == ["clean", "lint", "filter"] and scope == {}
    assert (sources[0].sha256, sources[0].page) == (lib.dryer, 2)
    assert sources[0].n == 1 and "lint filter" in sources[0].text
    assert "[" in sources[0].snip  # matches marked


def test_a_brand_or_model_the_question_names_narrows_it(lib):
    sources, terms, scope = ask.retrieve(lib, "how do I clean the Roborock filter?")
    assert scope == {"brand": "Roborock"} and "roborock" not in terms
    assert {s.sha256 for s in sources} == {lib.robot}
    _, _, scope = ask.retrieve(lib, "warranty on the dlex-5500v")
    assert scope == {"brand": "LG", "model": "DLEX5500V"}
    # A named brand whose pages don't hold the words: the whole library is searched instead.
    sources, _, scope = ask.retrieve(lib, "Roborock lint")
    assert scope == {} and sources[0].sha256 == lib.dryer


def test_a_picked_document_narrows_it_even_by_prefix(lib):
    sources, _, scope = ask.retrieve(lib, "clean filter", document=lib.robot[:10])
    assert scope == {"document": lib.robot} and {s.sha256 for s in sources} == {lib.robot}
    sources, _, _ = ask.retrieve(lib, "lint", brand="Roborock")  # a picked scope is never dropped
    assert sources == []


def test_excerpt_keeps_the_part_around_the_words():
    page = "filler words here. " * 200 + "Remove the drain pump filter and rinse it. " + "more filler. " * 200
    out = ask.excerpt(page, ["drain", "pump", "filter"], size=300)
    assert "drain pump filter" in out and len(out) <= 306
    assert out.startswith("… ") and out.endswith(" …")
    assert ask.excerpt("short page", ["x"]) == "short page"


# Answers


def test_no_model_gives_the_best_pages(lib):
    a = ask.ask(lib, "how do I reset the wifi?")
    assert a.status == "pages_only" and a.answer == "" and a.model is None
    assert (a.sources[0].sha256, a.sources[0].page) == (lib.robot, 2)
    assert a.headline.startswith("▲ ")


def test_the_model_reads_numbered_pages_and_cites_them(lib):
    m = FakeModel("Pull the lint filter straight up [1, 9]. Roll the lint off [1] .")
    a = ask.ask(lib, "how do I clean the lint filter?", m)
    assert a.status == "answered" and a.model == "ollama fake" and not a.leaves_network
    assert a.answer == "Pull the lint filter straight up [1]. Roll the lint off [1]."
    assert [s.cited for s in a.sources] == [True] + [False] * (len(a.sources) - 1)
    assert a.headline == f"✓ answered from 1 of {len(a.sources)} pages"
    system, user = m.sent[0]
    assert "NOT IN MANUALS" in system and "[n]" in system
    assert "[1] LG DLEX5500V, Owner's Manual, page 2:" in user and "roll the lint off" in user
    assert user.rstrip().endswith("Question: how do I clean the lint filter?")


def test_the_manuals_dont_say(lib):
    a = ask.ask(lib, "how do I clean the lint filter?", FakeModel("not in manuals"))
    assert a.status == "not_found" and a.answer == "The manuals don't say." and a.sources
    assert a.headline.startswith("– the manuals don't say")


def test_an_answer_that_cites_nothing_says_so(lib):
    a = ask.ask(lib, "how do I clean the lint filter?", FakeModel("Pull it out."))
    assert a.status == "answered" and a.headline.startswith("▲ answered, but the model cited none")


def test_a_model_that_fails_still_gives_the_pages(lib):
    a = ask.ask(lib, "reset wifi", FakeModel(error="Ollama didn't answer: timed out"))
    assert a.status == "model_error" and a.sources and "timed out" in a.headline
    assert a.headline.startswith("✗ ")


def test_nothing_matches(lib):
    a = ask.ask(lib, "zebra", FakeModel("never asked"))
    assert a.status == "no_pages" and a.sources == [] and a.headline.startswith("– ")
    a = ask.ask(lib, "how do I?", FakeModel("never asked"))  # only little words
    assert a.status == "no_pages"


def test_to_dict_leaves_out_the_page_text(lib):
    d = ask.ask(lib, "lint filter", FakeModel("Pull it up [1].")).to_dict()
    assert d["headline"].startswith("✓") and "text" not in d["sources"][0]
    assert d["sources"][0]["label"] == "LG DLEX5500V · Owner's Manual · p. 2"


# Providers


def test_ollama_and_anthropic_requests(monkeypatch):
    api = FakeAPI({"message": {"content": "Do this [1]."}})
    m = ask.make_model(AIConfig(provider="ollama", url="http://192.168.1.50:11434/"), api)
    assert m.complete("SYS", "USER") == "Do this [1]."
    sent = api.sent[0]
    assert sent["url"] == "http://192.168.1.50:11434/api/chat"
    assert sent["json"]["model"] == "gemma3" and sent["json"]["stream"] is False
    assert sent["json"]["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    api = FakeAPI({"content": [{"type": "text", "text": "Answer [2]."}], "stop_reason": "end_turn"})
    m = ask.make_model(AIConfig(provider="anthropic"), api)
    assert m.leaves_network and m.complete("SYS", "USER") == "Answer [2]."
    body = api.sent[0]["json"]
    assert body["system"] == "SYS" and body["messages"] == [{"role": "user", "content": "USER"}]
    assert api.sent[0]["headers"]["x-api-key"] == "test-key"

    with pytest.raises(ask.AskError, match="HTTP 500"):
        ask.make_model(AIConfig(provider="ollama"), FakeAPI({"error": "boom"}, status=500)).complete("s", "u")
    assert ask.make_model(AIConfig(provider="none"), api) is None
    with pytest.raises(ask.AskError):
        ask.make_model(AIConfig(provider="openai"), api)


# The command line and MCP


def test_cli_ask_prints_the_best_pages(cfg, lib, tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text(f"home = '{tmp_path.as_posix()}'\n", encoding="utf-8")
    assert cli.main(["--config", str(config), "ask", "how do I reset the wifi?"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("▲ the best pages (no AI model") and "[1] Roborock S8 · User Manual · p. 2" in out
    assert cli.main(["--config", str(config), "ask", "zebra"]) == 1
    assert "– no page of the library matches" in capsys.readouterr().out


def test_mcp_ask_manuals_is_read_only(cfg, lib):
    pytest.importorskip("mcp.server.mcpserver")
    from manualbeagle.mcp_server import build

    server = build(cfg)
    out = asyncio.run(server.call_tool("ask_manuals", {"question": "how do I clean the lint filter?"}))
    assert "pages_only" in str(out) and "DLEX5500V" in str(out)


def test_web_api_ask(cfg, lib):
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from manualbeagle.web import app as web

    base = "http://127.0.0.1:8765"
    with TestClient(web.create_app(cfg, http=FakeHTTP(b"")), base_url=base) as tc:
        assert tc.post("/api/ask", json={}).status_code == 400
        assert tc.post("/api/ask", json={"question": "x", "document": "nope"}).status_code == 400
        assert tc.post("/api/ask", json={"question": "x", "pages": 50}).status_code == 400
        a = tc.post("/api/ask", json={"question": "how do I reset the wifi?"}).json()
        assert a["status"] == "pages_only" and a["sources"][0]["page"] == 2
        assert "" in a["sources"][0]["snip"]  # the page's marks, not brackets
        a = tc.post("/api/ask", json={"question": "clean filter", "document": lib.dryer}).json()
        assert {s["sha256"] for s in a["sources"]} == {lib.dryer}
    cfg.ai = AIConfig(provider="ollama", url="http://192.168.1.50:11434")
    api = FakeAPI({"message": {"content": "Pull the lint filter straight up [1]."}})
    with TestClient(web.create_app(cfg, http=api), base_url=base) as tc:
        a = tc.post("/api/ask", json={"question": "how do I clean the lint filter?", "brand": "LG"}).json()
        assert a["status"] == "answered" and a["model"] == "ollama gemma3" and a["sources"][0]["cited"]
        assert a["scope"] == {"brand": "LG"} and api.sent[0]["url"].endswith("/api/chat")
        a = tc.post("/api/ask", json={"question": "lint filter", "use_model": False}).json()
        assert a["status"] == "pages_only" and len(api.sent) == 1


# Live: a real model


@pytest.mark.live
def test_live_ollama_answers_and_cites(lib):
    url = os.environ.get("MANUALBEAGLE_TEST_OLLAMA")
    if not url:
        pytest.skip("set MANUALBEAGLE_TEST_OLLAMA to an Ollama address")
    cfg = Config(
        ai=AIConfig(provider="ollama", url=url, model=os.environ.get("MANUALBEAGLE_TEST_OLLAMA_MODEL", ""))
    )
    with ask.model_client(cfg) as http:
        model = ask.make_model(cfg.ai, http)
        a = ask.ask(lib, "How do I clean the lint filter?", model)
        assert a.status == "answered" and any(s.cited and s.sha256 == lib.dryer for s in a.sources)
        assert ask.ask(lib, "How do I bake bread?", model).status in ("not_found", "no_pages")
