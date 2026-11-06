"""sync's decisions for one item: upload a PDF that names the model, link the maker's page, never a stranger's link."""

import argparse
from pathlib import Path

from manualbeagle import sync
from manualbeagle.adapters import Registry
from manualbeagle.config import Config
from manualbeagle.http import FetchError
from manualbeagle.library import Document
from manualbeagle.models import Candidate, DocKind, FindResult, Query
from manualbeagle.normalize import normalise
from manualbeagle.sinks.homebox import Item

ARGS = argparse.Namespace(min_score=60, links_only=False, force=False, allow_search_links=False)


class FakeLib:
    def __init__(self, check_score: float = 40, fail: bool = False, reasons: list[str] | None = None) -> None:
        self.check_score, self.fail, self.reasons = check_score, fail, reasons or ["checked"]

    def fetch(self, c, q, http, item=None):
        if self.fail:
            raise FetchError("HTTP 404")
        return Document(
            "abc",
            Path("x.pdf"),
            c.url,
            c.title,
            "owners_manual",
            "LG",
            q.model,
            ["p"],
            False,
            self.check_score,
            self.reasons,
        )

    def linked(self, brand, model):
        return []


def _patch(monkeypatch, cands):
    def fake_find(q, reg, http, engine):
        return FindResult(query=normalise(q), candidates=cands)

    monkeypatch.setattr(sync, "find", fake_find)


def item(**kw):
    return Item(
        id="I1",
        name="Washer",
        manufacturer=kw.get("brand", "LG"),
        model=kw.get("model", "WM5700HVA"),
        attachments=kw.get("attachments", []),
    )


def test_uploads_a_pdf_that_names_the_model(monkeypatch):
    _patch(
        monkeypatch,
        [
            Candidate(
                url="https://gscs-b2c.lge.com/f",
                title="OM",
                kind=DocKind.owners_manual,
                source="adapter:lg",
                score=92,
            )
        ],
    )
    p = sync.plan_item(item(), Config(), Registry(), None, FakeLib(40), ARGS)
    assert p.action == "upload" and p.url == "https://gscs-b2c.lge.com/f"


def test_links_the_maker_when_the_pdf_text_doesnt_match(monkeypatch):
    _patch(
        monkeypatch, [Candidate(url="https://gscs-b2c.lge.com/f", title="OM", source="adapter:lg", score=92)]
    )
    p = sync.plan_item(item(), Config(), Registry(), None, FakeLib(-10), ARGS)
    assert p.action == "link" and p.url.startswith("https://gscs-b2c.lge.com/")


def test_never_links_a_search_result_off_the_makers_domains(monkeypatch):
    _patch(
        monkeypatch,
        [
            Candidate(
                url="https://random.example/lg.pdf", title="<b>LG</b>", source="search:searxng", score=70
            )
        ],
    )
    p = sync.plan_item(item(), Config(), Registry(), None, FakeLib(fail=True), ARGS)
    assert p.action == "none" and "--allow-search-links" in p.reason


def test_skips_items_with_a_manual_or_no_model(monkeypatch):
    _patch(monkeypatch, [])
    assert sync.plan_item(item(model=""), Config(), Registry(), None, FakeLib(), ARGS).action == "skip"
    has = item(attachments=[{"type": "manual", "title": "x"}])
    assert sync.plan_item(has, Config(), Registry(), None, FakeLib(), ARGS).action == "skip"


def test_plain_titles():
    assert sync.plain("<b>Owner's</b>\x07 manual\n x") == "Owner's manual x"


def test_batch_coverage_counts_manuals_by_brand():
    from manualbeagle.batch import coverage

    plans = [
        sync.Plan("Washer", "1", "LG", "WM5700HVA", "upload"),
        sync.Plan("Dryer", "2", "LG", "DLEX5500V", "link"),
        sync.Plan("Freezer", "3", "GE", "", "skip"),
    ]
    text = coverage(plans)
    assert "All items" in text and "✓ 1 of 3 with a manual" in text
    assert "LG" in text and "▲ 1 maker's link" in text


def test_related_model_in_pdf_text():
    from manualbeagle.verify import related_model

    assert related_model("Models RCHT9610WFW2004, RCHT9510WFW2001", "RCHT9610WF") == "RCHT9610WFW2004"
    assert related_model("DCS380 reciprocating saw", "DCS380P1") == "DCS380"
    assert related_model("Model WM4000HWA", "WM5700HVA") is None


def test_uploads_an_adapters_pdf_that_names_a_related_model(monkeypatch):
    _patch(
        monkeypatch, [Candidate(url="https://gscs-b2c.lge.com/f", title="OM", source="adapter:lg", score=92)]
    )
    lib = FakeLib(-2, reasons=["PDF text names a related model (WM5700HVAW) -5"])
    p = sync.plan_item(item(), Config(), Registry(), None, lib, ARGS)
    assert p.action == "upload" and "related model (WM5700HVAW)" in p.reason


def test_a_search_result_naming_a_related_model_is_not_uploaded(monkeypatch):
    _patch(
        monkeypatch,
        [Candidate(url="https://www.lg.com/x.pdf", title="OM", source="search:searxng", score=70)],
    )
    lib = FakeLib(-2, reasons=["PDF text names a related model (WM5700HVAW) -5"])
    p = sync.plan_item(item(), Config(), Registry(), None, lib, ARGS)
    assert p.action == "link"


def test_family_manual_from_a_flagged_adapter_is_uploaded(monkeypatch):
    _patch(
        monkeypatch,
        [Candidate(url="https://reolink.com/m.pdf", title="Manual", source="adapter:reolink", score=90)],
    )
    lib = FakeLib(-7, reasons=["PDF text doesn't name the model -10"])
    p = sync.plan_item(item(brand="Reolink", model="D340W"), Config(), Registry(), None, lib, ARGS)
    assert p.action == "upload" and "family manual" in p.reason


def test_export_plans_copies_chosen_manuals_and_indexes_every_row(tmp_path):
    from manualbeagle.sinks.folder import export_plans

    pdf = tmp_path / "lib.pdf"
    pdf.write_bytes(b"%PDF-1.7 test")
    plans = [
        sync.Plan(
            "Washer",
            "1",
            "LG",
            "WM5700HVA",
            "upload",
            url="https://x/om.pdf",
            kind="owners_manual",
            file=str(pdf),
        ),
        sync.Plan("Dimmer", "2", "Lutron", "PD-6WCL", "link", url="https://lutron.example/pd-6wcl"),
        sync.Plan("Freezer", "3", "GE", "", "skip", reason="no manufacturer or model number"),
    ]
    index = export_plans(plans, tmp_path / "out", source="house.yaml")
    assert (tmp_path / "out" / "LG" / "WM5700HVA - Owner's manual.pdf").read_bytes() == b"%PDF-1.7 test"
    text = index.read_text(encoding="utf-8")
    assert (
        "✓ manual: **Washer**" in text and "▲ link: **Dimmer**" in text and "– skipped: **Freezer**" in text
    )


def test_keeps_a_manual_an_earlier_run_found(monkeypatch, tmp_path):
    from test_library import FakeHTTP, make_pdf

    from manualbeagle.library import Library

    lib = Library(tmp_path / "library", tmp_path / "db.sqlite")
    q = normalise(Query(brand="LG", model="WM5700HVA"))
    pdf = make_pdf(["OWNER'S MANUAL WM5700HVA washer", "Clean the door seal"])
    lib.fetch(
        Candidate(url="https://www.lg.com/m.pdf", title="Owner's Manual", kind=DocKind.owners_manual),
        q,
        FakeHTTP(pdf),
    )
    _patch(monkeypatch, [])  # this run's search finds nothing
    p = sync.plan_item(item(), Config(), Registry(), None, lib, ARGS)
    assert p.action == "upload" and p.url == "https://www.lg.com/m.pdf" and "earlier run" in p.reason
    other = sync.plan_item(item(model="WM4000HWA"), Config(), Registry(), None, lib, ARGS)
    assert other.action == "none"  # another model gets nothing from it
    lib.close()


def _sync_args(tmp_path, **kw):
    return argparse.Namespace(
        item=None,
        dry_run=False,
        report=None,
        metrics=str(tmp_path / "manualbeagle_sync.prom"),
        **{**vars(ARGS), **kw},
    )


def _metric(text: str, name: str) -> float:
    return next(float(line.split()[-1]) for line in text.splitlines() if line.startswith(name + " "))


def test_metrics_file_says_what_the_run_did(monkeypatch, tmp_path):
    class HB:
        def items(self):
            return [item(), item(model="")]

        def attach_file(self, *a):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sync, "homebox", lambda cfg: HB())
    monkeypatch.setattr(sync, "Library", lambda *a: type("L", (FakeLib,), {"close": lambda self: None})())
    _patch(
        monkeypatch, [Candidate(url="https://lg.com/m.pdf", title="OM", kind=DocKind.owners_manual, score=90)]
    )
    cfg = Config(home=tmp_path)
    cfg.search.engine = "none"
    assert sync.sync(_sync_args(tmp_path), cfg) == 0
    prom = (tmp_path / "manualbeagle_sync.prom").read_text(encoding="utf-8")
    assert _metric(prom, "manualbeagle_sync_success") == 1
    assert _metric(prom, "manualbeagle_sync_attached") == 1
    assert 'manualbeagle_sync_items{action="upload"} 1' in prom
    assert 'manualbeagle_sync_items{action="skip"} 1' in prom
    assert _metric(prom, "manualbeagle_sync_last_run_timestamp_seconds") > 1.7e9
    assert not (tmp_path / "manualbeagle_sync.prom.tmp").exists()


def test_metrics_file_is_written_when_homebox_cant_be_read(monkeypatch, tmp_path):
    from manualbeagle.sinks.homebox import HomeboxError

    def broken(cfg):
        raise HomeboxError("HTTP 401")

    monkeypatch.setattr(sync, "homebox", broken)
    assert sync.sync(_sync_args(tmp_path), Config(home=tmp_path)) == 1
    prom = (tmp_path / "manualbeagle_sync.prom").read_text(encoding="utf-8")
    assert _metric(prom, "manualbeagle_sync_success") == 0
    assert 'manualbeagle_sync_items{action="none"} 0' in prom


def test_a_refused_write_fails_the_run(monkeypatch, tmp_path):
    from manualbeagle.sinks.homebox import HomeboxError

    class HB:
        def items(self):
            return [item()]

        def attach_file(self, *a):
            raise HomeboxError("HTTP 500")

        def close(self):
            pass

    monkeypatch.setattr(sync, "homebox", lambda cfg: HB())
    monkeypatch.setattr(sync, "Library", lambda *a: type("L", (FakeLib,), {"close": lambda self: None})())
    _patch(
        monkeypatch, [Candidate(url="https://lg.com/m.pdf", title="OM", kind=DocKind.owners_manual, score=90)]
    )
    cfg = Config(home=tmp_path)
    cfg.search.engine = "none"
    assert sync.sync(_sync_args(tmp_path), cfg) == 1
    prom = (tmp_path / "manualbeagle_sync.prom").read_text(encoding="utf-8")
    assert _metric(prom, "manualbeagle_sync_success") == 0 and _metric(prom, "manualbeagle_sync_refused") == 1
