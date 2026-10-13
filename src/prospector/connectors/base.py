"""Connector contract and a polite shared HTTP client."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from ..branding import PROJECT_URL, __version__
from ..resolve import CompanyRecord


def user_agent() -> str:
    """The identity every discovery request carries, so a site owner can see who is reading and why.

    PROSPECTOR_USER_AGENT replaces it outright; otherwise PROSPECTOR_CONTACT_EMAIL is appended to the
    product string. Read from the environment (not Settings) because the crawler is a child process.
    """
    explicit = os.environ.get("PROSPECTOR_USER_AGENT", "").strip()
    if explicit:
        return explicit
    contact = os.environ.get("PROSPECTOR_CONTACT_EMAIL", "").strip()
    return f"Exora/{__version__} (+{PROJECT_URL}{'; ' + contact if contact else ''})"


@dataclass
class ParamSpec:
    name: str
    label: str
    kind: str = "text"  # text | textarea | select | int | ids (list of company ids, set by the app)
    required: bool = False
    options: list[str] | None = None
    default: Any = None
    help: str = ""
    hidden: bool = False  # set by the app (e.g. "enrich these companies"), not shown in the run form


class Connector:
    """Subclasses set the class attributes and implement fetch(params) -> iterator of CompanyRecord."""

    key: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    licence: ClassVar[str]
    params: ClassVar[list[ParamSpec]] = []
    needs_key: ClassVar[str | None] = None  # settings attribute holding an API key, if any
    needs_db: ClassVar[bool] = False  # reads companies from the database (enrichment, maintenance)
    maintenance: ClassVar[bool] = False  # works on existing records instead of yielding new ones

    def __init__(self, http: PoliteClient, api_key: str | None = None) -> None:
        self.http = http
        self.api_key = api_key

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:  # pragma: no cover - abstract
        raise NotImplementedError

    @classmethod
    def describe(cls, available: bool = True) -> dict[str, Any]:
        return {
            "key": cls.key,
            "name": cls.name,
            "description": cls.description,
            "licence": cls.licence,
            "needs_key": cls.needs_key,
            "available": available,
            "params": [p.__dict__ for p in cls.params if not p.hidden],
        }


class PoliteClient:
    """httpx client with a per-host minimum interval and retry/back-off on 429/5xx.

    The per-host clock is shared by every client in the process, so concurrent runs (several worker threads,
    several automations) still respect one request rate per external host.
    """

    _last: dict[str, float] = {}
    _lock = threading.Lock()

    def __init__(self, min_interval: float = 1.0, timeout: float = 60.0, transport: httpx.BaseTransport | None = None):
        self.client = httpx.Client(
            headers={"User-Agent": user_agent(), "Accept": "application/json, text/html;q=0.9, */*;q=0.5"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self.min_interval = min_interval

    def _wait(self, host: str, interval: float) -> None:
        # Reserve the next slot for this host under the lock, then sleep outside it, so a slow host never
        # blocks requests to other hosts from other threads.
        with self._lock:
            slot = max(time.monotonic(), self._last.get(host, 0.0) + interval)
            self._last[host] = slot
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def request(
        self, method: str, url: str, *, interval: float | None = None, retries: int = 3, **kw
    ) -> httpx.Response:
        host = httpx.URL(url).host
        delay = 2.0
        for attempt in range(retries + 1):
            self._wait(host, self.min_interval if interval is None else interval)
            try:
                r = self.client.request(method, url, **kw)
            except httpx.TransportError:
                if attempt == retries:
                    raise
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code in (429, 502, 503, 504) and attempt < retries:
                retry_after = r.headers.get("Retry-After", "")
                time.sleep(float(retry_after) if retry_after.isdigit() else delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r
        raise RuntimeError("unreachable")

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> httpx.Response:
        return self.request("POST", url, **kw)

    def close(self) -> None:
        self.client.close()
