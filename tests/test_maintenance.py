"""Maintenance suggestions, offline: rules on fixture manual text, a fake model, the store, and a fake Homebox."""

import datetime as dt
import json
import time
from pathlib import Path

import httpx
import pytest
import respx

from manualbeagle import cli
from manualbeagle import maintenance as mt
from manualbeagle.library import Library
from manualbeagle.sinks.homebox import Homebox, Item

FIXTURES = Path(__file__).parent / "fixtures" / "maintenance"


def pages(name: str) -> list[str]:
    """A fixture manual's pages (separated by form feeds)."""
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8").split("\f")


def found(name: str) -> list[tuple[int, str, str]]:
    return [(f.page, f.task, f.interval.label) for f in mt.rules_for_document(pages(name))]


# Intervals


@pytest.mark.parametrize(
    "text, label",
    [
        ("every two weeks", "every 2 weeks"),
        ("Every 3-6 months", "every 3 months"),
        ("once every year", "every year"),
        ("once or twice a month", "every month"),
        ("at least twice a month", "every 2 weeks"),
        ("twice a year", "every 6 months"),
        ("monthly", "every month"),
        ("quarterly", "every 3 months"),
        ("every other week", "every 2 weeks"),
        ("EVERY YEAR", "every year"),
        ("every five years", "every 5 years"),
    ],
)
def test_intervals(text, label):
    iv = mt.parse_interval(text)
    assert iv is not None and iv.label == label


@pytest.mark.parametrize(
    "text", ["daily", "every day", "after each use", "every 50 hours", "every few months"]
)
def test_no_reminder_for_short_or_vague_intervals(text):
    assert mt.parse_interval(text) is None


def test_next_date_by_the_calendar():
    assert mt.Interval(1, "month").after(dt.date(2026, 1, 31)) == dt.date(2026, 2, 28)
    assert mt.Interval(2, "week").after(dt.date(2026, 11, 3)) == dt.date(2026, 11, 17)
    assert mt.Interval(1, "year").after(dt.date(2028, 2, 29)) == dt.date(2029, 2, 28)


# Rules


def test_sentences_bullets_and_passives():
    assert found("window_ac") == [
        (2, "Clean the filter", "every 2 weeks"),
        (2, "Clean the air filter", "every 2 weeks"),  # "• Clean every 2 weeks" under "Air filter (...)"
        (2, "Check the drain pan", "every month"),
        (2, "Clean the drain pan", "every year"),  # "... checked once per month and cleaned once a year"
        (2, "Check the coils", "every 3 months"),  # "..., or at least twice a year" is the same task again
    ]


def test_no_tasks_from_timers_warranties_or_daily_chores():
    tasks = [t for _, t, _ in found("window_ac")]
    assert not any("timer" in t.lower() or "compressor" in t.lower() or "grille" in t.lower() for t in tasks)


def test_quotes_and_pages():
    f = mt.rules_for_document(pages("window_ac"))[0]
    assert f.page == 2 and f.quote == "CARE AND CLEANING: Clean the filter every two weeks."


def test_schedule_headings_and_rows():
    assert found("garage_opener") == [
        (1, "Test the safety reversal system", "every month"),
        (1, "Oil door rollers", "every year"),
        (1, "Test the battery backup", "every year"),
        (1, "Check the fuel tube", "every 2 years"),
    ]


def test_frequency_table():
    assert found("robot_vacuum") == [
        (1, "Clean the filter", "every week"),
        (1, "Replace the filter", "every 3 months"),
        (1, "Clean the side brush", "every month"),
        (1, "Replace the side brush", "every 3 months"),
        (1, "Clean the sensors", "every month"),
        (1, "Replace the water tank filters", "every 6 months"),
    ]


def test_mends_split_ligatures():
    assert mt.clean_text("Clean the fi lter and the fl ue") == "Clean the filter and the flue"


# The optional model


def fake_model(answer: dict | str, prompts: list | None = None):
    def ask(prompt: str) -> str:
        if prompts is not None:
            prompts.append(prompt)
        return answer if isinstance(answer, str) else json.dumps(answer)

    return ask


def test_model_tidies_and_adds_but_is_checked_against_the_page():
    text = pages("garage_opener")[0]
    rules = mt.rules_for_page(text, 1)
    prompts: list[str] = []
    answer = {
        "tasks": [
            # tidies a rule's finding: same interval, overlapping quote
            {"task": "Oil the door rollers, bearings and hinges", "interval": "every year", "quote": "Oil door rollers, bearings and hinges"},
            # adds one the rules left out
            {"task": "Check the door opens fully", "interval": "every month", "quote": "Check to be sure the door opens and closes fully."},
            # refused: not on the page
            {"task": "Grease the tracks", "interval": "every year", "quote": "Grease the door tracks every year"},
            # refused: the page never says weekly
            {"task": "Test the battery", "interval": "every week", "quote": "Test the battery backup"},
        ]
    }  # fmt: skip
    out = mt.ai_for_page(fake_model(answer, prompts), "Acme B1 manual", 1, text, rules, "ollama gemma4")
    by_task = {f.task: f for f in out}
    assert by_task["Oil the door rollers, bearings and hinges"].source == "rules+ollama gemma4"
    assert "Oil door rollers" not in by_task  # replaced, not repeated
    assert by_task["Check the door opens fully"].source == "ollama gemma4"
    assert "Grease the tracks" not in by_task and "Test the battery" not in by_task
    # The model can't remove a rule's finding, and it's shown the rules' findings.
    assert by_task["Test the safety reversal system"].source == "rules"
    assert "Test the safety reversal system (every month)" in prompts[0]


def test_model_answers_that_arent_json():
    with pytest.raises(mt.ModelError):
        mt.ai_for_page(fake_model("I found nothing."), "x", 1, "Clean the filter every week.", [], "m")
    assert mt.parse_tasks('Sure! {"tasks": []}') == []


# Matching items


def test_match_items_by_brand_alias_and_model_spelling():
    items = [
        Item(id="1", name="Thermostat", manufacturer="Honeywell Home", model="RCHT9610WF"),
        Item(id="2", name="Washer", manufacturer="LG", model="WM-5700 HVA"),
        Item(id="3", name="Other washer", manufacturer="Samsung", model="WM5700HVA"),
    ]
    assert [i.id for i in mt.match_items("Resideo", "rcht9610wf", items)] == ["1"]
    assert [i.id for i in mt.match_items("LG", "WM5700HVA", items)] == ["2"]
    assert mt.match_items("LG", "", items) == []


# The store and a whole run


@pytest.fixture
def lib(tmp_path):
    lib = Library(tmp_path / "library", tmp_path / "mb.sqlite")
    for sha, name, brand, model in (
        ("a" * 64, "window_ac", "LG", "AC8000"),
        ("b" * 64, "robot_vacuum", "eufy", "X8"),
    ):
        texts = pages(name)
        lib.db.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sha, str(tmp_path / f"{sha}.pdf"), "https://example.com/m.pdf", f"{brand} {model} Owner's Manual",
             "owners_manual", brand, model, len(texts), 0, time.time()),
        )  # fmt: skip
        lib.db.executemany(
            "INSERT INTO pages (text, sha256, page, brand, model, kind) VALUES (?,?,?,?,?,?)",
            [(t, sha, i + 1, brand, model, "owners_manual") for i, t in enumerate(texts)],
        )
        lib.db.execute("INSERT INTO links VALUES (?,?,?,?)", (sha, brand, model, ""))
    lib.db.commit()
    yield lib
    lib.close()


ITEMS = [Item(id="I-ac", name="Window AC", manufacturer="LG", model="AC8000")]


def test_suggest_keeps_new_findings_once(lib):
    counts = list(mt.suggest(lib, ITEMS))
    assert [(c.brand, c.found, c.added, c.items) for c in counts] == [
        ("LG", 5, 5, ["Window AC"]),
        ("eufy", 6, 6, []),
    ]
    store = mt.Store(lib)
    rows = store.list()
    ac = [s for s in rows if s.item_id == "I-ac"]
    assert {s.task for s in ac} >= {"Clean the filter", "Check the drain pan"}
    assert all(s.status == "new" and s.quote and s.page == 2 for s in ac)
    eufy = [s for s in rows if s.brand == "eufy"]
    assert {s.item_name for s in eufy} == {""}  # no Homebox item: kept by brand and model

    again = list(mt.suggest(lib, ITEMS))  # nothing is suggested twice
    assert sum(c.added for c in again) == 0 and len(store.list()) == 11


def test_dismissed_isnt_suggested_again_and_can_be_undone(lib):
    list(mt.suggest(lib, ITEMS))
    store = mt.Store(lib)
    s = store.list()[0]
    assert store.dismiss(s.id) and not store.dismiss(s.id)
    list(mt.suggest(lib, ITEMS))
    assert store.get(s.id).status == "dismissed" and store.counts() == {"new": 10, "filed": 0, "dismissed": 1}
    assert store.reopen(s.id) and store.get(s.id).status == "new"


def test_an_item_found_later_takes_over_a_waiting_suggestion(lib):
    list(mt.suggest(lib, None))  # no Homebox
    assert {s.item_id for s in mt.Store(lib).list()} == {""}
    added = sum(c.added for c in mt.suggest(lib, ITEMS))
    rows = mt.Store(lib).list()
    assert added == 0 and len(rows) == 11
    assert {s.item_name for s in rows if s.brand == "LG"} == {"Window AC"}


def test_suggest_with_a_model(lib):
    answer = {
        "tasks": [{"task": "Rinse the filter", "interval": "every 2 weeks", "quote": "Clean every 2 weeks"}]
    }
    counts = list(mt.suggest(lib, ITEMS, fake_model(answer), "ollama gemma4", sha="a" * 64))
    assert len(counts) == 1 and counts[0].pages_asked == 1
    sources = {s.task: s.source for s in mt.Store(lib).list()}
    assert sources["Rinse the filter"] == "rules+ollama gemma4"


def test_a_model_run_after_a_rules_run_rewords_rather_than_repeats(lib):
    list(mt.suggest(lib, ITEMS))
    store = mt.Store(lib)
    before = len(store.list())
    answer = {
        "tasks": [{"task": "Rinse the filter", "interval": "every 2 weeks", "quote": "Clean every 2 weeks"}]
    }
    added = sum(c.added for c in mt.suggest(lib, ITEMS, fake_model(answer), "ollama gemma4", sha="a" * 64))
    rows = store.list()
    assert added == 0 and len(rows) == before
    reworded = [s for s in rows if s.task == "Rinse the filter"]
    assert len(reworded) == 1 and reworded[0].source == "rules+ollama gemma4"
    assert "Clean the air filter" not in {s.task for s in rows}


def test_a_failing_model_leaves_the_rules(lib):
    def broken(prompt):
        raise mt.ModelError("Ollama didn't answer")

    c = next(mt.suggest(lib, ITEMS, broken, "ollama gemma4"))
    assert c.found == 5 and c.error == "Ollama didn't answer"


# Filing in Homebox


class FakeHomebox:
    """Records maintenance entries; refuses an item id when told to."""

    def __init__(self, refuse: str = "") -> None:
        self.refuse = refuse
        self.entries: list[tuple] = []

    def add_maintenance(self, entity_id, name, description, scheduled):
        from manualbeagle.sinks.homebox import HomeboxError

        if entity_id == self.refuse:
            raise HomeboxError("HTTP 404")
        self.entries.append((entity_id, name, description, scheduled))
        return {"id": f"E{len(self.entries)}"}


def test_file_in_homebox(lib):
    list(mt.suggest(lib, ITEMS))
    s = next(s for s in mt.Store(lib).list() if s.task == "Clean the drain pan")
    hb = FakeHomebox()
    entry = mt.file_in_homebox(hb, s, "I-ac", s.task, dt.date(2027, 11, 3))
    item, name, desc, when = hb.entries[0]
    assert (entry, item, name, when) == ("E1", "I-ac", "Clean the drain pan", "2027-11-03")
    assert "Repeat every year" in desc and "page 2" in desc and "LG AC8000 Owner's Manual" in desc


@respx.mock
def test_homebox_maintenance_api():
    base = "http://homebox.test:7745/api/v1"
    route = respx.post(f"{base}/entities/I1/maintenance").mock(
        return_value=httpx.Response(
            201, json={"id": "M1", "name": "Clean the filter", "scheduledDate": "2026-11-17"}
        )
    )
    respx.get(f"{base}/entities/I1/maintenance").mock(return_value=httpx.Response(200, json=[{"id": "M1"}]))
    hb = Homebox("http://homebox.test:7745", "k")
    assert hb.add_maintenance("I1", "Clean the filter", "d", "2026-11-17")["id"] == "M1"
    sent = json.loads(route.calls[0].request.content)
    assert sent == {
        "name": "Clean the filter",
        "description": "d",
        "scheduledDate": "2026-11-17",
        "cost": "0",
    }
    assert hb.maintenance("I1") == [{"id": "M1"}]


# The command line


def test_cli_suggest_and_list(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text(f"home = '{tmp_path.as_posix()}'\n", encoding="utf-8")
    lib = Library(tmp_path / "library", tmp_path / "manualbeagle.sqlite")
    texts = pages("robot_vacuum")
    lib.db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("c" * 64, "x.pdf", "https://example.com", "Manual", "owners_manual", "eufy", "X8", 1, 0, 0.0),
    )
    lib.db.executemany(
        "INSERT INTO pages (text, sha256, page, brand, model, kind) VALUES (?,?,?,?,?,?)",
        [(t, "c" * 64, i + 1, "eufy", "X8", "owners_manual") for i, t in enumerate(texts)],
    )
    lib.db.commit()
    lib.close()
    assert cli.main(["--config", str(config), "maintenance", "suggest"]) == 0
    out = capsys.readouterr().out
    assert "✓ eufy X8: 6 found, 6 new" in out and "✓ 6 new suggestions" in out
    assert cli.main(["--config", str(config), "maintenance", "list"]) == 0
    out = capsys.readouterr().out
    assert "● new" in out and "Replace the filter, every 3 months" in out and "page 1" in out
    assert cli.main(["--config", str(config), "maintenance", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 6 and rows[0]["every"] == "every week"
    assert cli.main(["--config", str(config), "maintenance", "suggest", "--ai"]) == 2  # no [ai] model
