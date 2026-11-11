"""The Settings page's changes: settings.py, config.build, and the web API, offline."""

import json

import pytest

from manualbeagle import config, settings
from manualbeagle.config import Config

# The settings module and config layering


def test_check_values():
    assert settings.check("delay", "2") == 2.0
    assert settings.check("search.url", "http://10.0.0.5:8080/") == "http://10.0.0.5:8080"
    assert settings.check("ai.provider", "ollama") == "ollama"
    for key, bad in [
        ("delay", 0.1),
        ("respect_robots", "yes"),
        ("ai.provider", "qwen"),
        ("search.url", "file:///etc/passwd"),
        ("homebox.url", "http://user:pw@host"),
    ]:
        with pytest.raises(ValueError):
            settings.check(key, bad)


@pytest.mark.parametrize(
    "key", ["home", "adapter_dirs", "homebox.token_command", "homebox.token_file", "ai.api_key_env"]
)
def test_settings_that_could_run_programs_or_read_secrets_are_not_editable(key):
    with pytest.raises(ValueError):
        settings.check(key, "x")
    with pytest.raises(ValueError):
        settings.changed({}, {key: None})


def test_saved_changes_win_over_the_file_and_env_over_both(tmp_path, monkeypatch):
    monkeypatch.delenv("MANUALBEAGLE_SEARXNG", raising=False)
    monkeypatch.delenv("MANUALBEAGLE_HOMEBOX_URL", raising=False)
    path = tmp_path / "config.toml"
    path.write_text(f'home = "{tmp_path.as_posix()}"\ndelay = 3\n[search]\nurl = "http://file:8080"\n')
    settings.save(tmp_path, {"delay": 1, "ai.provider": "ollama"})
    cfg = config.load(path)
    assert cfg.delay == 1 and cfg.ai.provider == "ollama" and cfg.search.url == "http://file:8080"
    monkeypatch.setenv("MANUALBEAGLE_SEARXNG", "http://env:8080")
    assert config.load(path).search.url == "http://env:8080"
    settings.save(tmp_path, {"delay": None})  # taken away: the file's value again
    assert config.load(path).delay == 3


def test_a_broken_settings_file_is_ignored(tmp_path):
    (tmp_path / "settings.json").write_text("not json")
    assert settings.read_overrides(tmp_path) == {}
    (tmp_path / "settings.json").write_text(json.dumps({"homebox.token_command": "rm -rf /", "delay": 2}))
    assert settings.read_overrides(tmp_path) == {"delay": 2}


def test_describe_sources(tmp_path):
    file_data = {"delay": 3}
    out = {f["key"]: f for f in settings.describe(file_data, {"ai.provider": "ollama"},
                                                  Config(delay=3).model_dump(mode="json"),
                                                  {"search.url": "MANUALBEAGLE_SEARXNG"})}  # fmt: skip
    assert out["delay"]["source"] == "file" and out["ai.provider"]["source"] == "page"
    assert out["search.url"]["source"] == "env" and out["homebox.url"]["source"] == "default"


# The web API

starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402
from test_library import FakeHTTP  # noqa: E402

from manualbeagle.web import app as web  # noqa: E402

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def client(tmp_path):
    file_data = {"home": tmp_path.as_posix(), "search": {"engine": "none"}}
    cfg = config.build(file_data)
    with TestClient(web.create_app(cfg, http=FakeHTTP(b""), file_data=file_data), base_url=BASE) as tc:
        yield tc


def test_settings_page_reads_and_saves(client, tmp_path):
    r = client.get("/api/settings").json()
    fields = {f["key"]: f for f in r["fields"]}
    assert fields["search.engine"]["value"] == "none" and fields["search.engine"]["source"] == "file"
    assert r["keys"]["homebox"] is False
    r = client.post(
        "/api/settings", json={"changes": {"ai.provider": "ollama", "ai.url": "http://10.0.0.9:11434"}}
    )
    assert r.status_code == 200
    fields = {f["key"]: f for f in r.json()["fields"]}
    assert fields["ai.provider"]["value"] == "ollama" and fields["ai.provider"]["source"] == "page"
    st = client.app.state.mb
    assert st.cfg.ai.provider == "ollama" and st.cfg.ollama_url() == "http://10.0.0.9:11434"
    assert json.loads((tmp_path / "settings.json").read_text())["ai.provider"] == "ollama"


def test_settings_refuses_bad_values_and_locked_keys(client, tmp_path):
    assert client.post("/api/settings", json={"changes": {"delay": 0}}).status_code == 400
    r = client.post("/api/settings", json={"changes": {"homebox.token_command": "id"}})
    assert r.status_code == 400 and "can't be changed" in r.json()["error"]
    assert not (tmp_path / "settings.json").exists()


def test_settings_changes_need_this_origin(client):
    r = client.post(
        "/api/settings", json={"changes": {"delay": 2}}, headers={"origin": "https://evil.example"}
    )
    assert r.status_code == 403


def test_a_key_goes_only_to_the_address_set_beside_it(monkeypatch):
    from manualbeagle.config import build
    from manualbeagle.sinks.paperless import PaperlessError
    from manualbeagle.sinks.paperless import from_config as paperless
    from manualbeagle.sources.homeassistant import HomeAssistantError
    from manualbeagle.sources.homeassistant import from_config as home_assistant

    monkeypatch.setenv("PAPERLESS_TOKEN", "secret")
    monkeypatch.setenv("HA_TOKEN", "secret")
    monkeypatch.delenv("MANUALBEAGLE_PAPERLESS_URL", raising=False)
    monkeypatch.delenv("MANUALBEAGLE_HA_URL", raising=False)
    file_data = {"paperless": {"url": "http://paperless.test:8000"}}
    # The file's address: the key goes there, also with a path or a trailing slash changed on the page.
    assert paperless(build(file_data, {})).base == "http://paperless.test:8000/api"
    assert paperless(build(file_data, {"paperless.url": "http://PAPERLESS.test:8000/"})).base
    # Another host from the page: the key is held back.
    cfg = build(file_data, {"paperless.url": "http://elsewhere.test"})
    assert "only sent to" in cfg.paperless.key_held_back(cfg.paperless.url)
    with pytest.raises(PaperlessError, match="only sent to"):
        paperless(cfg)
    # An address only the page names: no key at all.
    with pytest.raises(HomeAssistantError, match="no address"):
        home_assistant(build({}, {"home_assistant.url": "http://ha.test:8123"}))
    # The environment's address counts as set beside the key.
    monkeypatch.setenv("MANUALBEAGLE_HA_URL", "http://ha.test:8123")
    assert home_assistant(build({}, {})).url == "http://ha.test:8123"


def test_the_homebox_key_is_bound_too(monkeypatch):
    from manualbeagle.config import build
    from manualbeagle.sinks.homebox import HomeboxError
    from manualbeagle.sync import homebox

    monkeypatch.setenv("HOMEBOX_TOKEN", "k")
    monkeypatch.delenv("MANUALBEAGLE_HOMEBOX_URL", raising=False)
    file_data = {"homebox": {"url": "http://homebox.test:7745"}}
    assert homebox(build(file_data, {"homebox.url": "http://homebox.test:7745/"})).base.startswith(
        "http://homebox"
    )
    with pytest.raises(HomeboxError, match="only sent to"):
        homebox(build(file_data, {"homebox.url": "http://elsewhere.test:7745"}))


def test_the_donetick_key_is_bound_too(monkeypatch):
    from manualbeagle.config import build
    from manualbeagle.sinks.donetick import DonetickError
    from manualbeagle.sinks.donetick import from_config as donetick

    monkeypatch.setenv("DONETICK_TOKEN", "k")
    monkeypatch.delenv("MANUALBEAGLE_DONETICK_URL", raising=False)
    file_data = {"donetick": {"url": "http://donetick.test:2021"}, "maintenance": {"target": "donetick"}}
    assert donetick(build(file_data, {})).base == "http://donetick.test:2021/api/v1"
    with pytest.raises(DonetickError, match="only sent to"):
        donetick(build(file_data, {"donetick.url": "http://elsewhere.test:2021"}))
    # The target and the label may change on the page; the token's source may not.
    cfg = build(file_data, {"maintenance.target": "homebox", "donetick.label": "House"})
    assert cfg.maintenance.target == "homebox" and cfg.donetick.label == "House"
    with pytest.raises(ValueError):
        settings.check("maintenance.target", "todoist")
    with pytest.raises(ValueError):
        settings.check("donetick.token_file", "/etc/shadow")
    with pytest.raises(ValueError):
        build({"maintenance": {"target": "todoist"}}, {})
