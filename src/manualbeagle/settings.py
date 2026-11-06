"""The settings a person may change on the web UI's Settings page, kept in `settings.json` beside the library.

config.toml (or its defaults) stays the base, written by whoever installs Manual Beagle. `settings.json` holds only
what was changed on the Settings page, and wins over the file; the environment variables in
`config.ENV_OVERRIDES` win over both, because they belong to whoever runs the service.

Some settings are never editable here, so the page can't run a program, read a secret or move the library:
`home`, `adapter_dirs`, every table's `token_env`, `token_file`, `token_command` and `token_command_env`
(`[homebox]`, `[home_assistant]`, `[paperless]`), `[home_assistant] skip_integrations`, `[paperless] tags`, and
`[ai] api_key_env`. They stay in config.toml.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

FILE_NAME = "settings.json"


@dataclass(frozen=True)
class Setting:
    """One editable setting: its dotted key in the config ("search.url"), and how the page shows it."""

    key: str
    label: str
    help: str
    kind: str  # number | bool | choice | text | url
    choices: tuple[str, ...] = ()
    low: float = 0
    high: float = 0


SETTINGS: tuple[Setting, ...] = (
    Setting("delay", "Pause between requests to one site", "In seconds. Lower is faster and less polite.",
            "number", low=0.5, high=30),
    Setting("respect_robots", "Obey robots.txt", "Off by default: a lookup is one person asking for one manual, "
            "not a crawler.", "bool"),
    Setting("search.engine", "Web search", "The fallback when no adapter knows the brand.", "choice",
            ("searxng", "none")),
    Setting("search.url", "SearXNG address", "Its JSON API, such as http://localhost:8080.", "url"),
    Setting("homebox.url", "Homebox address", "Empty turns Homebox off. Its API key stays in the config file "
            "or the service's environment, and is sent only to the address set there.", "url"),
    Setting("homebox.attach_links", "Attach the maker's link when there's no PDF", "", "bool"),
    Setting("home_assistant.url", "Home Assistant address", "Such as http://homeassistant.local:8123. Empty "
            "turns it off. Manual Beagle only reads its devices. Its token stays in the config file or the "
            "service's environment, and is sent only to the address set there.", "url"),
    Setting("paperless.url", "Paperless-ngx address", "Such as http://paperless.local:8000. Empty turns it "
            "off. Its API token stays in the config file or the service's environment, and is sent only to "
            "the address set there.", "url"),
    Setting("paperless.auto", "File every new manual in Paperless", "Within a few minutes of its download. "
            "Off: send them from the Library page.", "bool"),
    Setting("paperless.document_type", "Paperless document type", "Made in Paperless when it's missing; "
            "empty sets none.", "text"),
    Setting("ai.provider", "AI model (rating plates, Ask)", "Reads a plate photo beside OCR, and writes Ask's "
            "answers. Ollama runs on your own machine; Claude sends the photo, or the question and the pages' "
            "text, to Anthropic.", "choice", ("none", "ollama", "anthropic")),
    Setting("ai.model", "Model", "Empty means the default: gemma3 for Ollama, claude-opus-5-5 for Claude.", "text"),
    Setting("ai.url", "Ollama address", "Such as http://localhost:11434. Only for Ollama.", "url"),
)  # fmt: skip
BY_KEY = {s.key: s for s in SETTINGS}


def overrides_path(home: Path) -> Path:
    """Where the Settings page keeps its changes: `settings.json` in the library's home folder."""
    return Path(home) / FILE_NAME


def read_overrides(home: Path) -> dict[str, Any]:
    """The saved changes, by dotted key. Unknown keys and a file that isn't a JSON object are ignored."""
    try:
        data = json.loads(overrides_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k in BY_KEY} if isinstance(data, dict) else {}


def get(data: dict, key: str) -> Any:
    """The value at a dotted key in nested config data, or None."""
    for part in key.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def merge(data: dict, overrides: dict[str, Any]) -> dict:
    """A copy of nested config data with each dotted key in `overrides` set."""
    out = json.loads(json.dumps(data, default=str))
    for key, value in overrides.items():
        *tables, name = key.split(".")
        node = out
        for t in tables:
            node = node.setdefault(t, {})
        node[name] = value
    return out


def check(key: str, value: Any) -> Any:
    """`value` as the setting `key` stores it. Raises ValueError for a key that isn't editable or a bad value."""
    s = BY_KEY.get(key)
    if s is None:
        raise ValueError(f"{key} can't be changed on the Settings page")
    if s.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{s.label}: on or off")
        return value
    if s.kind == "number":
        try:
            n = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{s.label}: a number") from None
        if not s.low <= n <= s.high:
            raise ValueError(f"{s.label}: between {s.low:g} and {s.high:g}")
        return n
    text = str(value if value is not None else "").strip()
    if len(text) > 300:
        raise ValueError(f"{s.label}: at most 300 characters")
    if s.kind == "choice" and text not in s.choices:
        raise ValueError(f"{s.label}: one of {', '.join(s.choices)}")
    if s.kind == "url" and text:
        u = urlsplit(text)
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
            raise ValueError(f"{s.label}: an http:// or https:// address, with no login in it")
        text = text.rstrip("/")
    return text


def changed(current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """`current` saved changes with `changes` applied. Raises ValueError and changes nothing.

    Each value is checked (`check`); None takes a change away, so the config file's value (or the default)
    counts again.
    """
    out = dict(current)
    for key, value in changes.items():
        if value is None:
            if key not in BY_KEY:
                raise ValueError(f"{key} can't be changed on the Settings page")
            out.pop(key, None)
        else:
            out[key] = check(key, value)
    return out


def save(home: Path, changes: dict[str, Any]) -> dict[str, Any]:
    """Apply `changes` to settings.json (`changed`) and return all the saved changes."""
    new = changed(read_overrides(home), changes)
    write(home, new)
    return new


def write(home: Path, overrides: dict[str, Any]) -> None:
    """Replace settings.json with `overrides`, in one step."""
    path = overrides_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def describe(file_data: dict, overrides: dict[str, Any], effective: dict, env: dict[str, str]) -> list[dict]:
    """Each setting for the page: its value now, where that comes from, and the config file's value.

    `file_data` is config.toml as read, `effective` the resulting config (`Config.model_dump()`), `env` the
    dotted keys an environment variable sets, with the variable's name. `source` is "env", "page", "file" or
    "default".
    """
    out = []
    for s in SETTINGS:
        if s.key in env:
            source = "env"
        elif s.key in overrides:
            source = "page"
        elif get(file_data, s.key) is not None:
            source = "file"
        else:
            source = "default"
        out.append({
            **asdict(s),
            "choices": list(s.choices),
            "value": get(effective, s.key),
            "source": source,
            "env": env.get(s.key, ""),
            "file_value": get(file_data, s.key),
        })  # fmt: skip
    return out
