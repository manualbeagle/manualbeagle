"""The command line, offline: commands that must refuse before touching the network."""

from manualbeagle import cli


def test_fetch_refuses_a_manual_mirror(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text(f"home = '{tmp_path.as_posix()}'\n", encoding="utf-8")
    code = cli.main(
        [
            "--config",
            str(config),
            "fetch",
            "https://www.manualslib.com/manual/1/x.html",
            "--brand",
            "LG",
            "--model",
            "X",
        ]
    )
    assert code == 1
    assert "✗ not downloaded: a manual mirror" in capsys.readouterr().out
    assert not (tmp_path / "manualbeagle.sqlite").exists()  # refused before the library was opened
