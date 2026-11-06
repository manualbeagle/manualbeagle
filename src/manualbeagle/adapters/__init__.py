"""Adapter registry: every YAML file in defs/, plus any extra folders the user configures."""

from __future__ import annotations

import importlib
from pathlib import Path

import yaml

from ..normalize import BRAND_ALIASES, brand_key
from .base import Adapter, AdapterSpec, Context, TestCase
from .yaml_adapter import YamlAdapter

DEFS = Path(__file__).parent / "defs"

__all__ = ["Adapter", "AdapterSpec", "Context", "Registry", "TestCase", "load_spec"]


def load_spec(path: Path) -> AdapterSpec:
    """Read an adapter's YAML file. Its id defaults to the file name without .yaml."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.setdefault("id", path.stem)
    return AdapterSpec.model_validate(data)


def build(spec: AdapterSpec) -> Adapter:
    if spec.python:
        mod_name, _, cls_name = spec.python.partition(":")
        mod = importlib.import_module(f"{__name__}.py.{mod_name}")
        return getattr(mod, cls_name)(spec)
    return YamlAdapter(spec)


class Registry:
    """Every adapter, from the built-in defs/ folder and any extra folders, looked up by id or brand."""

    def __init__(self, extra_dirs: list[Path] | None = None) -> None:
        self.adapters: dict[str, Adapter] = {}
        for d in [DEFS, *(extra_dirs or [])]:
            for p in sorted(d.glob("*.yaml")):
                a = build(load_spec(p))
                self.adapters[a.id] = a  # a user's folder can override a built-in by id
        self._by_brand: dict[str, list[Adapter]] = {}
        for a in self.adapters.values():
            for name in a.names():
                listed = self._by_brand.setdefault(brand_key(name), [])
                if a not in listed:  # "Levoit" and its alias "LEVOIT" are one key
                    listed.append(a)

    def __iter__(self):
        return iter(self.adapters.values())

    def get(self, adapter_id: str) -> Adapter:
        """The adapter with this id. Raises KeyError."""
        return self.adapters[adapter_id]

    def download_refusal(self, url: str) -> str:
        """Why `url` must not be downloaded, or "" when it may be.

        A manual mirror's terms forbid automated downloads, so a URL on one is a link for a person to open. Used
        where a user or an AI hands in a URL directly (`fetch`, the MCP server's `fetch_manual`), since those skip
        ranking, which would have marked it link-only. A brand marked `blocked` isn't refused here: its documents
        are often on another host that answers (Canon's on gdlp01.c-wss.com), and the client stops at a real bot
        wall anyway.
        """
        from ..verify import is_mirror

        if is_mirror(url):
            return "a manual mirror, whose terms forbid automated downloads: open it yourself"
        return ""

    def for_brand(self, brand: str) -> list[Adapter]:
        """The adapters for a brand name, its aliases or a shared alias in normalize.py; may be empty."""
        key = brand_key(brand)
        found = self._by_brand.get(key)
        if found:
            return found
        alias = BRAND_ALIASES.get(key)
        if alias:
            return self._by_brand.get(brand_key(alias), [])
        return []
