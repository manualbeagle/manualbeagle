"""One TOML file. Secrets come from environment variables, files or commands it names, never from the file itself."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr


def default_home() -> Path:
    """Where the library and database live: MANUALBEAGLE_HOME, else the platform's per-user data folder."""
    if env := os.environ.get("MANUALBEAGLE_HOME"):
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "manualbeagle"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "manualbeagle"


def default_config_path() -> Path:
    """Where config.toml is read from: MANUALBEAGLE_CONFIG, else the platform's per-user config folder."""
    if env := os.environ.get("MANUALBEAGLE_CONFIG"):
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "manualbeagle" / "config.toml"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "manualbeagle" / "config.toml"


class SearchConfig(BaseModel):
    """The `[search]` table: the web-search fallback's engine and address."""

    engine: str = "searxng"  # "none" turns the web-search fallback off
    url: str = "http://localhost:8080"


class TokenSource(BaseModel):
    """Where a service's API key or token is read from. The key itself is never in the config file."""

    token_env: str = ""  # an environment variable holding it; each table sets its own default
    token_file: str = ""  # a file holding it; read when the env variable is empty
    # A command that prints it (a password manager's CLI, a decrypting helper), run when neither the
    # environment variable nor the file has it. A list of arguments, or one string split like a shell would.
    token_command: list[str] | str = Field(default_factory=list)
    token_command_env: dict[str, str] = Field(default_factory=dict)  # extra environment variables for it

    # The origin the key may be sent to: the address set beside it, in config.toml or the environment. None
    # means any (a Config made in code). `build` sets it, so an address changed on the Settings page alone
    # never receives the key: whoever can change settings could otherwise send the key to their own server.
    _key_origin: str | None = PrivateAttr(default=None)

    def token(self) -> str:
        """The key: from `token_env`, else `token_file`, else `token_command`. Empty when none has it."""
        return read_secret(self.token_env, self.token_file, self.token_command, self.token_command_env)

    def bind_key(self, url: str) -> None:
        """Send the key only to `url`'s origin from now on ("" sends it nowhere)."""
        self._key_origin = origin(url)

    def key_held_back(self, url: str) -> str:
        """Why the key isn't sent to `url`, or "" when it may be."""
        if self._key_origin is None or origin(url) == self._key_origin:
            return ""
        where = self._key_origin or "no address"
        return (
            f"its key is only sent to the address set beside it in the config file or the environment "
            f"({where}), not to {url} from the Settings page"
        )


class HomeboxConfig(TokenSource):
    """The `[homebox]` table: the Homebox address and where to read its API key."""

    url: str = ""
    token_env: str = "HOMEBOX_TOKEN"
    attach_links: bool = True  # attach the maker's URL when a manual can't be downloaded


# Integrations whose devices are never physical things, besides those Home Assistant marks as services.
HA_SKIP_INTEGRATIONS = [
    "sun", "backup", "hassio", "homeassistant", "cloud", "met", "moon", "season", "workday", "time_date",
    "forecast_solar", "google_translate", "radio_browser", "shopping_list", "template", "group",
]  # fmt: skip


class HomeAssistantConfig(TokenSource):
    """The `[home_assistant]` table: a Home Assistant to read devices from, and where to read its token.

    Manual Beagle only reads from Home Assistant: a long-lived token of a non-admin, read-only user is enough.
    """

    url: str = ""
    token_env: str = "HA_TOKEN"
    # Integrations (domains such as "sun") whose devices aren't offered. Devices Home Assistant marks as
    # services, and those of helper integrations, are skipped as well.
    skip_integrations: list[str] = Field(default_factory=lambda: list(HA_SKIP_INTEGRATIONS))


class PaperlessConfig(TokenSource):
    """The `[paperless]` table: a Paperless-ngx to file manuals in, and where to read its API token."""

    url: str = ""
    token_env: str = "PAPERLESS_TOKEN"
    auto: bool = False  # file every new manual in the library by itself, a few minutes after it's downloaded
    document_type: str = "Manual"  # made when missing; empty sets none
    tags: list[str] = Field(default_factory=lambda: ["manual"])  # besides the brand and model tags


class DonetickConfig(TokenSource):
    """The `[donetick]` table: a Donetick to make recurring chores in, and where to read its API token.

    Used when `[maintenance] target = "donetick"`: each approved maintenance suggestion becomes a chore that
    repeats on the suggestion's interval, assigned to the token's user.
    """

    url: str = ""
    token_env: str = "DONETICK_TOKEN"
    label: str = ""  # a label the chores get, by name; it must exist in Donetick. Empty: none
    due_time: str = "09:00"  # the time of day a chore falls due
    timezone: str = ""  # an IANA time zone such as "Europe/London"; empty: this computer's own
    rolling: bool = False  # true: the next date counts from when it's done, not from when it was due
    notify: bool = True  # Donetick reminds the person when the chore falls due


class MaintenanceConfig(BaseModel):
    """The `[maintenance]` table: where an approved maintenance suggestion goes."""

    # homebox: a one-off maintenance entry on the Homebox item. donetick: a Donetick chore that repeats.
    target: Literal["homebox", "donetick"] = "homebox"


class AIConfig(BaseModel):
    """The `[ai]` table, for optional AI plugins. Nothing requires it; the default is no AI.

    Used by the nameplate reader's vision model (`vision.py`) and by Ask's answers (`ask.py`): `ollama` or
    `anthropic`. A model that reads photos (such as gemma3 or gemma4) serves both.
    """

    provider: str = "none"  # none | anthropic | ollama
    model: str = ""  # default: gemma3 for ollama, claude-opus-5-5 for anthropic
    url: str = ""  # ollama's address (default http://localhost:11434)
    api_key_env: str = ""  # anthropic: the variable holding the API key (default ANTHROPIC_API_KEY)


class Config(BaseModel):
    """The whole configuration file. Every setting has a default."""

    home: Path = Field(default_factory=default_home)
    delay: float = 1.5  # seconds between requests to one site
    # robots.txt is for crawlers. A lookup is one person asking for one product's manual, so it's off by default.
    respect_robots: bool = False
    adapter_dirs: list[Path] = Field(default_factory=list)
    search: SearchConfig = Field(default_factory=SearchConfig)
    homebox: HomeboxConfig = Field(default_factory=HomeboxConfig)
    home_assistant: HomeAssistantConfig = Field(default_factory=HomeAssistantConfig)
    paperless: PaperlessConfig = Field(default_factory=PaperlessConfig)
    donetick: DonetickConfig = Field(default_factory=DonetickConfig)
    maintenance: MaintenanceConfig = Field(default_factory=MaintenanceConfig)
    ai: AIConfig = Field(default_factory=AIConfig)

    @property
    def library(self) -> Path:
        """The folder that holds downloaded PDFs."""
        return self.home / "library"

    @property
    def db_path(self) -> Path:
        """The SQLite database with the documents table and the per-page full-text index."""
        return self.home / "manualbeagle.sqlite"

    def private_hosts(self) -> list[str]:
        """The origins of the user's own services, which may sit on private addresses. Nothing else may."""
        return [u for u in (self.search.url, self.homebox.url) if u]

    def ollama_url(self) -> str:
        """Ollama's address when `[ai] provider` is ollama, else "". Only the `[ai]` model's client may reach it."""
        return (self.ai.url or "http://localhost:11434") if self.ai.provider == "ollama" else ""

    def client(self, extra_private: list[str] | None = None, **kw):
        """A PoliteClient with this configuration's delay, robots.txt setting and allowed private hosts.

        `extra_private` adds origins this one client may reach on private addresses (the vision model's Ollama),
        so a lookup or download, whose URLs come from web sites, can't reach them.
        """
        from .http import PoliteClient

        kw.setdefault("respect_robots", self.respect_robots)
        private = self.private_hosts() + [u for u in (extra_private or []) if u]
        return PoliteClient(delay=self.delay, private_ok=private, **kw)


# Environment variables that set an address, by dotted key. They win over the file and the Settings page.
ENV_OVERRIDES = {
    "search.url": "MANUALBEAGLE_SEARXNG",
    "homebox.url": "MANUALBEAGLE_HOMEBOX_URL",
    "home_assistant.url": "MANUALBEAGLE_HA_URL",
    "paperless.url": "MANUALBEAGLE_PAPERLESS_URL",
    "donetick.url": "MANUALBEAGLE_DONETICK_URL",
}


# Tables whose key goes only to the address set beside it (`TokenSource.bind_key`).
KEY_BOUND = ("homebox", "home_assistant", "paperless", "donetick")


def read_file(path: Path | None = None) -> dict:
    """config.toml as read from `path` (default: `default_config_path()`); {} when there is no file."""
    path = path or default_config_path()
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def settings_home(file_data: dict) -> Path:
    """The home folder config.toml's data names (or the default one), where settings.json lives."""
    return Path(file_data["home"]) if file_data.get("home") else default_home()


def env_overrides() -> dict[str, str]:
    """The dotted keys an environment variable sets now (`ENV_OVERRIDES`), with the variable's name."""
    return {key: name for key, name in ENV_OVERRIDES.items() if os.environ.get(name)}


def build(file_data: dict, overrides: dict | None = None) -> Config:
    """The configuration from config.toml's data, the Settings page's saved changes, then the environment.

    The changes are `overrides` when given, else `settings.json` in the home folder the file names (or the
    default one). Raises pydantic's ValidationError (a ValueError) when the result isn't a valid configuration.
    """
    from . import settings

    if overrides is None:
        overrides = settings.read_overrides(settings_home(file_data))
    data = settings.merge(file_data, overrides)
    for key, name in env_overrides().items():
        data = settings.merge(data, {key: os.environ[name]})
    cfg = Config.model_validate(data)
    for table in KEY_BOUND:
        trusted = (
            os.environ.get(ENV_OVERRIDES[f"{table}.url"]) or settings.get(file_data, f"{table}.url") or ""
        )
        getattr(cfg, table).bind_key(str(trusted))
    return cfg


def origin(url: str) -> str:
    """scheme://host[:port] of a URL, lower-cased; "" for one without a host."""
    from urllib.parse import urlsplit

    u = urlsplit((url or "").strip())
    return f"{u.scheme.lower()}://{u.netloc.lower()}" if u.scheme and u.netloc else ""


def load(path: Path | None = None) -> Config:
    """Read the configuration from `path` (default: `default_config_path()`).

    A missing file means all defaults. Changes saved on the web UI's Settings page win over the file, and
    the environment variables in `ENV_OVERRIDES` win over both (`build`).
    """
    return build(read_file(path))


def read_secret(
    env: str,
    file: str = "",
    command: list[str] | str | None = None,
    command_env: dict[str, str] | None = None,
) -> str:
    """Read a secret from the environment variable `env`, else from `file`, else from what `command` prints.

    In a file or a command's output, a `token=...` line (or `<env>=...`, any case) wins; otherwise the first
    non-comment line is the secret, and an upper-case NAME=value line yields its value. A command that fails or
    prints nothing counts as no secret; its error is not shown, since it may echo the secret's neighbours.
    Returns an empty string when none has it.
    """
    if env and os.environ.get(env):
        return os.environ[env].strip()
    if file:
        p = Path(os.path.expandvars(os.path.expanduser(file)))
        if p.exists():
            found = _secret_from_text(p.read_text(encoding="utf-8"), env)
            if found:
                return found
    if command:
        args = shlex.split(command, posix=os.name != "nt") if isinstance(command, str) else list(command)
        try:
            r = subprocess.run(
                args,
                capture_output=True,
                timeout=60,
                env={**os.environ, **(command_env or {})},
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if r.returncode == 0:
            return _secret_from_text(r.stdout.decode("utf-8", "replace"), env)
    return ""


def _secret_from_text(text: str, env: str = "") -> str:
    """The secret in a file's or a command's text (see `read_secret`)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    names = {"token", env.lower()} - {""}
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.strip().lower() in names:
            return value.strip()
    if not lines:
        return ""
    name, sep, value = lines[0].partition("=")
    return value.strip() if sep and name.isupper() else lines[0]
