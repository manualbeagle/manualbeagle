import json

from manualbeagle import cli
from manualbeagle.adapters import Registry
from manualbeagle.scoreboard import breakage, build, live_cell
from manualbeagle.testing import run_case


def _result(adapter, model, status, message="", year=None, top=()):
    return {
        "adapter": adapter,
        "model": model,
        "year": year,
        "status": status,
        "message": message,
        "top": list(top),
    }


def test_a_case_result_as_json_carries_the_year_and_top_three():
    reg = Registry()
    a = reg.get("alorair")
    r = run_case(a, a.spec.tests[0], mode="replay")
    d = r.as_dict()
    assert d["adapter"] == "alorair"
    assert d["status"] == "pass"
    assert d["year"] is None
    assert len(d["top"]) <= 3
    assert {"score", "kind", "title", "url"} <= set(d["top"][0])
    json.dumps(d)  # plain data


def test_live_cell_pairs_every_status_with_a_glyph():
    assert live_cell([]) == "–"
    assert live_cell([_result("x", "A", "skip")]) == "–"
    assert live_cell([_result("x", "A", "pass"), _result("x", "B", "pass")]) == "✓ 2/2"
    cell = live_cell(
        [_result("x", "A", "pass"), _result("x", "B", "fail"), _result("x", "C", "error", year=2024)]
    )
    assert cell == "✗ 1/3: ✗ B, ! 2024 C"


def test_the_scoreboard_gets_a_live_column_only_from_live_results():
    plain = build()
    assert "| Live |" not in plain
    assert "every night" in plain
    live = build(live=[_result("alorair", "Galaxy 60", "pass"), _result("alorair", "Sentinel HD55", "fail")])
    assert "| Brand | Status | Tested models | Live | Notes |" in live
    row = next(line for line in live.splitlines() if line.startswith("| **AlorAir**"))
    assert "✗ 1/2: ✗ Sentinel HD55" in row
    # an adapter with no live results shows a dash, not a pass
    canon = next(line for line in live.splitlines() if line.startswith("| **Canon**"))
    assert "| – |" in canon


def test_breakage_lists_only_broken_cases_grouped_by_adapter():
    results = [
        _result("alorair", "Galaxy 60", "pass"),
        _result(
            "alorair",
            "Sentinel HD55",
            "fail",
            "no result in the top 8 met the expectation",
            top=[
                {"score": 40, "kind": "spec_sheet", "title": "Spec", "url": "https://www.alorair.com/x.pdf"}
            ],
        ),
        _result("levoit", "Core 300", "blocked", "HTTP 403"),
        _result("canon", "X", "skip", "blocked site"),
    ]
    text, n = breakage(results, run_url="https://example.invalid/run/1")
    assert n == 2
    assert "**1 of 3 cases passed**" in text
    assert "[the run](https://example.invalid/run/1)" in text
    assert "### AlorAir (`alorair`, ✓ works)" in text
    assert "✗ fail: `Sentinel HD55`" in text
    assert "`https://www.alorair.com/x.pdf`" in text
    assert "✗ blocked: `Core 300`: `HTTP 403`" in text
    assert "Galaxy 60" not in text  # passes aren't listed
    assert "canon" not in text  # nor skips


def test_breakage_of_a_clean_run_says_so():
    text, n = breakage([_result("alorair", "Galaxy 60", "pass")])
    assert n == 0
    assert "✓ Every case that ran passed." in text


def test_cli_test_json_then_report_and_live_scoreboard(tmp_path, capsys):
    out = tmp_path / "results.json"
    assert cli.main(["adapters", "test", "alorair", "--json", str(out)]) == 0
    results = json.loads(out.read_text(encoding="utf-8"))
    assert [r["status"] for r in results] == ["pass", "pass"]

    report = tmp_path / "report.md"
    assert cli.main(["adapters", "report", str(out), "--write", str(report)]) == 0
    assert "✓ Every case that ran passed." in report.read_text(encoding="utf-8")

    results[1]["status"] = "fail"
    out.write_text(json.dumps(results), encoding="utf-8")
    assert cli.main(["adapters", "report", str(out)]) == 1
    assert "✗ fail" in capsys.readouterr().out

    board = tmp_path / "brands.md"
    assert (
        cli.main(
            ["adapters", "scoreboard", "--live", str(out), "--live-when", "2026-11-05", "--write", str(board)]
        )
        == 0
    )
    text = board.read_text(encoding="utf-8")
    assert "on 2026-11-05" in text
    assert "✗ 1/2" in text


def test_breakage_keeps_site_text_out_of_markdown():
    hostile = {
        "score": 10,
        "kind": None,
        "title": "@octocat ![x](https://e.invalid/a.png) `x`\nnew line",
        "url": "https://e.invalid/<b>",
    }
    text, _ = breakage([_result("alorair", "M`1", "fail", "see [here](https://e.invalid)", top=[hostile])])
    line = next(x for x in text.splitlines() if "octocat" in x)
    assert line.count("`") == 4  # two code spans, none broken open by the title's own backticks
    assert "`@octocat ![x](https://e.invalid/a.png) 'x' new line`" in line
    assert "`see [here](https://e.invalid)`" in text
    assert "`M'1`" in text
