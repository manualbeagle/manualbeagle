import asyncio

import pytest

pytest.importorskip("mcp.server.mcpserver")

from manualbeagle.config import Config  # noqa: E402
from manualbeagle.mcp_server import build  # noqa: E402


def _names(server) -> set[str]:
    tools = asyncio.run(server.list_tools())
    return {t.name for t in tools}


def test_read_only_by_default(tmp_path):
    server = build(Config(home=tmp_path))
    assert _names(server) == {
        "find_manual",
        "search_manuals",
        "ask_manuals",
        "read_pages",
        "list_documents",
        "maintenance_suggestions",
    }


def test_maintenance_suggestions_tool(tmp_path):
    from test_maintenance import pages

    from manualbeagle import maintenance as mt
    from manualbeagle.library import Library

    cfg = Config(home=tmp_path)
    lib = Library(cfg.library, cfg.db_path)
    lib.db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("d" * 64, "x.pdf", "https://example.com", "Manual", "owners_manual", "eufy", "X8", 1, 0, 0.0),
    )
    lib.db.execute(
        "INSERT INTO pages (text, sha256, page, brand, model, kind) VALUES (?,?,?,?,?,?)",
        (pages("robot_vacuum")[0], "d" * 64, 1, "eufy", "X8", "owners_manual"),
    )
    lib.db.commit()
    list(mt.suggest(lib, None))
    lib.close()
    server = build(cfg)
    out = asyncio.run(server.call_tool("maintenance_suggestions", {"brand": "EUFY"}))
    assert "Replace the filter" in str(out) and "every 3 months" in str(out)
    with pytest.raises(Exception):  # noqa: B017
        asyncio.run(server.call_tool("maintenance_suggestions", {"status": "bogus"}))


def test_write_tools_need_the_flag(tmp_path):
    assert "fetch_manual" in _names(build(Config(home=tmp_path), allow_write=True))


def test_fetch_refuses_link_only_urls(tmp_path):
    server = build(Config(home=tmp_path), allow_write=True)
    for url in (
        "https://www.manualslib.com/manual/1/X.html",
        "https://www.ManualsLib.com./manual/1/X.html",  # case and a trailing dot
    ):
        out = asyncio.run(server.call_tool("fetch_manual", {"url": url, "brand": "X", "model": "Y1"}))
        assert "not downloaded" in str(out), url
