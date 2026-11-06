"""Polite HTTP: one request at a time per site, a delay between them, a real User-Agent, robots.txt on request.

The same client records and replays responses ("cassettes"), so adapter tests run offline.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from . import __version__

USER_AGENT = (
    f"ManualBeagle/{__version__} (+https://github.com/manualbeagle/manualbeagle; "
    "finds a manual for the person who owns the product)"
)

# Text that bot walls put on their challenge pages. Seeing one means stop, not retry.
_CHALLENGE_MARKERS = [
    "cf-chl-",
    "Attention Required! | Cloudflare",
    "<title>Just a moment...</title>",
    "_Incapsula_Resource",
    "px-captcha",
    "captcha-delivery.com",
    "Pardon Our Interruption",
    "awsWafCookieDomainList",
    "<title>Human Verification</title>",
    "/_sec/cp_challenge",
    "<title>Edge IP Restricted |",
]
# Only a challenge when the status is an error too: normal pages carry these on their forms.
_WEAK_MARKERS = ["g-recaptcha", "hcaptcha.com", "Access Denied", "challenge-platform"]


class FetchError(Exception):
    """A request failed: the network, an HTTP error the caller can't use, or a response too large."""

    pass


class Blocked(FetchError):
    """robots.txt, a bot wall or a CAPTCHA said no. We never get around these."""


class UnsafeURL(FetchError):
    """Not http(s), or the host is on a private, loopback or reserved address."""


class CassetteMiss(FetchError):
    """Replaying a cassette and the request wasn't recorded."""


@dataclass
class Response:
    """A finished response: status, final URL (after redirects), lower-cased headers and body bytes."""

    status: int
    url: str
    headers: dict[str, str]
    content: bytes

    @property
    def text(self) -> str:
        """The body decoded with the charset from Content-Type (UTF-8 by default), never raising."""
        ctype = self.headers.get("content-type", "")
        m = re.search(r"charset=([\w-]+)", ctype)
        enc = m.group(1) if m else "utf-8"
        try:
            return self.content.decode(enc, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """The body parsed as JSON."""
        return json.loads(self.text)

    @property
    def ok(self) -> bool:
        """True for a 2xx status."""
        return 200 <= self.status < 300

    @property
    def content_type(self) -> str:
        """The media type from Content-Type, lower case, without parameters."""
        return self.headers.get("content-type", "").split(";")[0].strip().lower()


def _key(method: str, url: str, params: dict | None, body: bytes | None) -> str:
    full = url + ("?" + urlencode(sorted((params or {}).items()), doseq=True) if params else "")
    h = hashlib.sha256(body or b"").hexdigest()[:12] if body else ""
    return f"{method.upper()} {full}" + (f" #{h}" if h else "")


@dataclass
class Cassette:
    """A file of recorded responses. mode is "replay" or "record"."""

    path: Path
    mode: str = "replay"
    entries: dict[str, dict] = field(default_factory=dict)
    max_body: int = 400_000  # don't keep PDFs or giant pages in the repo
    trim: Callable[[str, str], str] | None = None  # (url, body) -> the part of the body the adapter reads

    def __post_init__(self) -> None:
        if self.path.exists() and self.mode == "replay":
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = {e["key"]: e for e in data["interactions"]}

    def get(self, key: str) -> Response:
        """The recorded response for a request key. Raises CassetteMiss when it wasn't recorded."""
        e = self.entries.get(key)
        if e is None:
            raise CassetteMiss(f"not recorded in {self.path.name}: {key}")
        body = base64.b64decode(e["b64"]) if "b64" in e else e.get("text", "").encode("utf-8")
        return Response(e["status"], e["url"], e.get("headers", {}), body)

    def put(self, key: str, r: Response) -> None:
        """Record a response, trimmed by `trim` and capped at `max_body`. PDF bodies are never kept."""
        e: dict[str, Any] = {
            "key": key,
            "status": r.status,
            "url": r.url,
            "headers": {
                k: v for k, v in r.headers.items() if k in ("content-type", "location", "content-length")
            },
        }
        if r.content_type == "application/pdf" or r.content[:5] == b"%PDF-":
            e["text"] = "%PDF- (body not recorded)"
        else:
            body = r.content
            if self.trim:
                body = self.trim(r.url, r.text).encode("utf-8")
            body = body[: self.max_body]
            try:
                e["text"] = body.decode("utf-8")
            except UnicodeDecodeError:
                e["b64"] = base64.b64encode(body).decode()
        self.entries[key] = e

    def save(self) -> None:
        """Write the recorded responses to `path` as JSON, sorted by key so diffs stay small."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"interactions": sorted(self.entries.values(), key=lambda e: e["key"])}
        self.path.write_text(
            json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )


class PoliteClient:
    """The only way adapters reach the network.

    One request at a time per host with `delay` seconds between them, a User-Agent naming the project, a size
    cap (`max_body`), refusal of private and reserved addresses except the origins in `private_ok`, an
    optional robots.txt check (`respect_robots`), bot-wall detection (`Blocked`), and recording or replaying
    through a `Cassette`.
    """

    def __init__(
        self,
        delay: float = 1.5,
        timeout: float = 30.0,
        user_agent: str = USER_AGENT,
        respect_robots: bool = False,
        cassette: Cassette | None = None,
        private_ok: list[str] | None = None,
        max_body: int = 25_000_000,
        refuse: Callable[[str], str] | None = None,
    ) -> None:
        self.delay = delay
        self.user_agent = user_agent
        self.respect_robots = respect_robots
        self.cassette = cassette
        self.max_body = max_body
        # The user's own services (SearXNG, Homebox) live on private addresses; nothing else may.
        # Matched by whole origin (scheme, host, port), so another port on the same host stays refused.
        self.private_ok = {_origin(u) for u in (private_ok or [])}
        # Says why a URL must not be fetched ("" when it may be), on every hop: a URL handed in directly can
        # redirect to a manual mirror (Registry.download_refusal).
        self.refuse = refuse
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.8"},
            # Runs for every request, redirect hops included.
            event_hooks={"request": [self._check_target]},
        )
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._guard = threading.Lock()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _check_target(self, request: httpx.Request) -> None:
        """Refuse anything but http(s) to a public address, so a URL from a search result can't reach the LAN.

        Checked again on every redirect hop. (A DNS answer that changes between this check and the connection
        could still slip past; fetches run on the user's own machine, for the user, which limits the harm.)
        """
        url = request.url
        if url.scheme not in ("http", "https"):
            raise UnsafeURL(f"refusing a {url.scheme}: URL")
        host = (url.host or "").lower()
        if self.refuse and (reason := self.refuse(str(url))):
            raise UnsafeURL(f"refusing {host}: {reason}")
        if _origin(str(url)) in self.private_ok:
            return
        try:
            infos = socket.getaddrinfo(host, url.port or (443 if url.scheme == "https" else 80))
        except socket.gaierror as e:
            raise FetchError(f"can't resolve {host}: {e}") from e
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global or ip.is_multicast:
                raise UnsafeURL(f"refusing {host}: it resolves to a private or reserved address ({ip})")

    def _read_capped(self, r: httpx.Response, limit: int) -> bytes:
        buf = bytearray()
        for chunk in r.iter_bytes():
            buf += chunk
            if len(buf) > limit:
                raise FetchError(f"{r.url} answered with more than {limit} bytes")
        return bytes(buf)

    def _host_lock(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def _wait_turn(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.delay - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)

    def allowed(self, url: str) -> bool:
        """Whether robots.txt allows `url`; always True when `respect_robots` is off or a cassette replays."""
        if not self.respect_robots or (self.cassette and self.cassette.mode == "replay"):
            return True
        u = urlparse(url)
        root = f"{u.scheme}://{u.netloc}"
        if root not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                with self._client.stream("GET", root + "/robots.txt", timeout=15) as r:
                    if r.status_code >= 500:
                        rp = None  # can't tell: treat as disallowed, per the robots.txt spec
                    elif r.status_code >= 400:
                        rp.parse([])  # no robots.txt: everything allowed
                    else:
                        body = self._read_capped(r, 1_000_000)
                        rp.parse(body.decode("utf-8", errors="replace").splitlines())
            except (httpx.HTTPError, FetchError):
                rp.parse([])
            self._robots[root] = rp
        rp = self._robots[root]
        if rp is None:
            return False
        return rp.can_fetch(self.user_agent, url) and rp.can_fetch("ManualBeagle", url)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        data: dict | None = None,
        headers: dict | None = None,
        check_challenge: bool = True,
    ) -> Response:
        """Send one request politely and return the whole response.

        Replays from the cassette when there is one in replay mode, else waits its turn for the host, sends,
        and records in record mode. Raises Blocked for robots.txt (when respected), HTTP 429 or a bot-wall
        challenge page (unless `check_challenge` is False), UnsafeURL for a private address, and FetchError
        for network errors.
        """
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body, sort_keys=True).encode()
        elif data is not None:
            body = urlencode(sorted(data.items())).encode()
        key = _key(method, url, params, body)

        if self.cassette and self.cassette.mode == "replay":
            resp = self.cassette.get(key)
        else:
            if not self.allowed(url):
                raise Blocked(f"robots.txt disallows {url}")
            host = urlparse(url).netloc
            with self._host_lock(host):
                self._wait_turn(host)
                try:
                    with self._client.stream(
                        method, url, params=params, json=json_body, data=data, headers=headers
                    ) as r:
                        content = self._read_capped(r, self.max_body)
                        resp = Response(
                            r.status_code, str(r.url), {k.lower(): v for k, v in r.headers.items()}, content
                        )
                except httpx.HTTPError as e:
                    raise FetchError(f"{method} {url}: {e}") from e
                finally:
                    self._last[host] = time.monotonic()
            if self.cassette and self.cassette.mode == "record":
                self.cassette.put(key, resp)

        if check_challenge and (resp.status == 429 or _looks_like_challenge(resp)):
            raise Blocked(f"bot protection at {urlparse(resp.url).netloc} (HTTP {resp.status})")
        return resp

    def get(self, url: str, **kw: Any) -> Response:
        """Send a GET request; see `request`."""
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> Response:
        """Send a POST request; see `request`."""
        return self.request("POST", url, **kw)

    def download(self, url: str, dest: Path, max_bytes: int = 200_000_000) -> Response:
        """Stream a file to dest. Not recorded in cassettes."""
        if not self.allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        host = urlparse(url).netloc
        with self._host_lock(host):
            self._wait_turn(host)
            try:
                with self._client.stream("GET", url) as r:
                    headers = {k.lower(): v for k, v in r.headers.items()}
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    n = 0
                    head = b""
                    with open(dest, "wb") as f:
                        for chunk in r.iter_bytes():
                            n += len(chunk)
                            if n > max_bytes:
                                raise FetchError(f"{url} is larger than {max_bytes} bytes")
                            if len(head) < 4096:
                                head += chunk[: 4096 - len(head)]
                            f.write(chunk)
                    resp = Response(r.status_code, str(r.url), headers, head)
            except httpx.HTTPError as e:
                raise FetchError(f"GET {url}: {e}") from e
            finally:
                self._last[host] = time.monotonic()
        if resp.content_type == "text/html" and _looks_like_challenge(resp):
            raise Blocked(f"bot protection at {urlparse(resp.url).netloc}")
        return resp


def _origin(url: str) -> tuple[str, str, int]:
    u = httpx.URL(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    return (u.scheme, (u.host or "").lower(), port)


def _looks_like_challenge(r: Response) -> bool:
    if r.content[:5] == b"%PDF-":
        return False
    head = r.content[:20000].decode("utf-8", errors="ignore")
    if any(m in head for m in _CHALLENGE_MARKERS):
        return True
    # Weak markers also appear on ordinary pages (Cloudflare loads its challenge-platform script on many 404s),
    # so they count only on the statuses a challenge answers with.
    return r.status in (401, 403, 429, 503) and any(m in head for m in _WEAK_MARKERS)
