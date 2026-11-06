"""Optional vision-model plugins that read a rating plate photo, on top of the rules-only reader.

Nothing needs these. `nameplate.read_plate()` always runs first; a vision model only adds its own suggestion,
and only when the person asks for it (the web page's "Ask the vision model" button). They are set up in the
`[ai]` table of the config:

- `provider = "ollama"`: a model you run yourself with Ollama, such as `gemma3` or `llama3.2-vision`, at `url`
  (default `http://localhost:11434`). The photo stays on your network.
- `provider = "anthropic"`: Claude, through Anthropic's API, with the key in the environment variable named by
  `api_key_env` (default `ANTHROPIC_API_KEY`). The photo is sent to Anthropic.

Presets, defaults and docs use only non-Chinese models. Every request goes through `PoliteClient`, like all of
Manual Beagle's network access.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from .config import AIConfig, read_secret
from .http import FetchError
from .nameplate import Suggestion

PROMPT = (
    "This photo shows the rating plate (nameplate) of an appliance or tool. Read the brand (the maker's name), "
    "the model number and the serial number exactly as printed. Answer with only a JSON object with the keys "
    '"brand", "model" and "serial", using an empty string for anything you cannot read. Do not guess.'
)
DEFAULT_MODELS = {"ollama": "gemma3", "anthropic": "claude-opus-5-5"}
TIMEOUT = 180.0  # a local model on a CPU can take a minute or more


class VisionError(Exception):
    """A vision model that couldn't be asked or gave no usable answer, with the message the page shows."""


class Vision:
    """A vision model: `read()` sends one photo and returns its reading as suggestions."""

    provider = ""

    def __init__(self, cfg: AIConfig, http: Any) -> None:
        self.cfg = cfg
        self.http = http
        self.model = cfg.model or DEFAULT_MODELS[self.provider]

    @property
    def label(self) -> str:
        """The provider and model, as the page names it: "ollama gemma3"."""
        return f"{self.provider} {self.model}"

    @property
    def leaves_network(self) -> bool:
        """Whether the photo goes to a service outside your own network."""
        return self.provider != "ollama"

    def read(self, image: bytes, media_type: str) -> dict[str, list[Suggestion]]:
        """Ask the model about one photo. Returns {"brands": [...], "models": [...], "serials": [...]}."""
        answer = self._ask(base64.standard_b64encode(image).decode("ascii"), media_type)
        found = parse_answer(answer)
        why = f"read by {self.label}"
        out: dict[str, list[Suggestion]] = {"brands": [], "models": [], "serials": []}
        for key, field in (("brand", "brands"), ("model", "models"), ("serial", "serials")):
            value = str(found.get(key) or "").strip()
            if key != "brand":
                value = code_only(value)
            if value:
                out[field].append(Suggestion(value, why, source=self.label))
        return out

    def _ask(self, image_b64: str, media_type: str) -> str:
        raise NotImplementedError


class Ollama(Vision):
    """A vision model served by Ollama's chat API (`POST /api/chat`)."""

    provider = "ollama"

    def _ask(self, image_b64: str, media_type: str) -> str:
        url = (self.cfg.url or "http://localhost:11434").rstrip("/") + "/api/chat"
        body = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [{"role": "user", "content": PROMPT, "images": [image_b64]}],
        }
        try:
            r = self.http.post(url, json_body=body, check_challenge=False)
        except FetchError as e:
            raise VisionError(f"Ollama didn't answer: {e}") from None
        if not r.ok:
            raise VisionError(f"Ollama answered HTTP {r.status}: {_error(r)}")
        try:
            return r.json()["message"]["content"]
        except (ValueError, KeyError, TypeError):
            raise VisionError("Ollama's answer wasn't a chat message") from None


class Anthropic(Vision):
    """Claude, through Anthropic's Messages API (`POST /v1/messages`), with an image content block.

    Effort is low (reading a plate needs little thinking), and server-side fallback is on, so a request the
    model declines is retried on another model inside the same call.
    """

    provider = "anthropic"
    url = "https://api.anthropic.com/v1/messages"

    def _ask(self, image_b64: str, media_type: str) -> str:
        key = read_secret(self.cfg.api_key_env or "ANTHROPIC_API_KEY")
        if not key:
            raise VisionError(f"no API key: set {self.cfg.api_key_env or 'ANTHROPIC_API_KEY'}")
        body = {
            "model": self.model,
            "max_tokens": 16000,
            "output_config": {"effort": "low"},
            "fallbacks": "default",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": image_b64},
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "server-side-fallback-2026-07-01",
        }
        try:
            r = self.http.post(self.url, json_body=body, headers=headers, check_challenge=False)
        except FetchError as e:
            raise VisionError(f"Anthropic's API didn't answer: {e}") from None
        if not r.ok:
            raise VisionError(f"Anthropic's API answered HTTP {r.status}: {_error(r)}")
        try:
            data = r.json()
        except ValueError:
            raise VisionError("Anthropic's answer wasn't JSON") from None
        if data.get("stop_reason") == "refusal":
            raise VisionError("the model declined to read this photo")
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


PROVIDERS: dict[str, type[Vision]] = {"ollama": Ollama, "anthropic": Anthropic}


def make_vision(cfg: AIConfig, http: Any) -> Vision | None:
    """The vision model the `[ai]` table sets up, or None when it's off. Raises VisionError for an unknown one."""
    if cfg.provider in ("", "none"):
        return None
    cls = PROVIDERS.get(cfg.provider)
    if cls is None:
        raise VisionError(f"[ai] provider {cfg.provider!r} can't read photos; use ollama or anthropic")
    return cls(cfg, http)


def code_only(value: str) -> str:
    """A model or serial number without the words a model sometimes adds ("DCS355 CORDLESS MULTI-TOOL").

    The first word with a digit in it, if the answer has several words; otherwise the answer as it is.
    """
    words = value.split()
    if len(words) < 2:
        return value
    return next((w.strip(",;:") for w in words if any(c.isdigit() for c in w)), value)


def parse_answer(text: str) -> dict[str, str]:
    """The brand, model and serial from a model's answer: the first JSON object in it, values as stripped text.

    Raises VisionError when the answer holds no JSON object.
    """
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise VisionError("the model's answer had no JSON in it")
    try:
        data = json.loads(m.group(0))
    except ValueError:
        raise VisionError("the model's answer wasn't valid JSON") from None
    if not isinstance(data, dict):
        raise VisionError("the model's answer wasn't a JSON object")
    out = {}
    for key in ("brand", "model", "serial"):
        value = data.get(key)
        out[key] = str(value).strip()[:80] if isinstance(value, str | int) else ""
    return out


def _error(r: Any) -> str:
    try:
        data = r.json()
    except ValueError:
        return r.text[:200]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message", err))[:200]
    return str(err or data)[:200]
