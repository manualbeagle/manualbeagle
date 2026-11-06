"""Configuration: where the Homebox API key comes from (an environment variable, a file, or a command)."""

import sys

from manualbeagle.config import HomeboxConfig, read_secret


def test_env_wins(monkeypatch, tmp_path):
    f = tmp_path / "key"
    f.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv("HB_TEST_TOKEN", " from-env ")
    assert read_secret("HB_TEST_TOKEN", str(f)) == "from-env"


def test_file_forms(monkeypatch, tmp_path):
    monkeypatch.delenv("HB_TEST_TOKEN", raising=False)
    f = tmp_path / "key"
    f.write_text("# what it's for\nurl=https://homebox.example\ntoken=abc123\n", encoding="utf-8")
    assert read_secret("HB_TEST_TOKEN", str(f)) == "abc123"  # the token= line, not the first line
    f.write_text("# a comment\nHOMEBOX_TOKEN=def456\n", encoding="utf-8")
    assert read_secret("HB_TEST_TOKEN", str(f)) == "def456"  # an upper-case NAME=value line
    f.write_text("ghi789\n", encoding="utf-8")
    assert read_secret("HB_TEST_TOKEN", str(f)) == "ghi789"  # a bare key
    assert read_secret("HB_TEST_TOKEN", str(tmp_path / "missing")) == ""


def test_command_with_its_environment(monkeypatch):
    monkeypatch.delenv("HB_TEST_TOKEN", raising=False)
    cmd = [sys.executable, "-c", "import os; print('url=x'); print('token=' + os.environ['HB_HELPER'])"]
    cfg = HomeboxConfig(
        token_env="HB_TEST_TOKEN", token_command=cmd, token_command_env={"HB_HELPER": "jkl012"}
    )
    assert cfg.token() == "jkl012"


def test_command_that_fails_gives_no_secret(monkeypatch):
    monkeypatch.delenv("HB_TEST_TOKEN", raising=False)
    fails = [sys.executable, "-c", "import sys; print('token=leaked'); sys.exit(3)"]
    assert read_secret("HB_TEST_TOKEN", "", fails) == ""
    assert read_secret("HB_TEST_TOKEN", "", ["no-such-command-for-manualbeagle-tests"]) == ""
