"""In-process cache for expensive aggregates (facets, quality summary, data health).

Entries are keyed by a global data version: any write that changes prospect data calls `bump()`, so a cached
aggregate is never served after the data under it changed. A short TTL bounds staleness for changes made by
other processes (e.g. a separate worker on a server). Thread-safe; bounded size.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any

_lock = threading.Lock()
_version = 0
_store: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
MAX_ENTRIES = 512


def bump() -> None:
    """Invalidate every cached aggregate (call after any write to prospect data)."""
    global _version
    with _lock:
        _version += 1
        _store.clear()


def version() -> int:
    return _version


def cached(key: Hashable, ttl: float, compute: Callable[[], Any]) -> Any:
    now = time.monotonic()
    k = (_version, key)
    with _lock:
        hit = _store.get(k)
        if hit and hit[0] > now:
            _store.move_to_end(k)
            return hit[1]
    value = compute()  # computed outside the lock: slow queries never block other requests
    with _lock:
        if k[0] == _version:  # do not store a value computed against data that changed meanwhile
            _store[k] = (now + ttl, value)
            _store.move_to_end(k)
            while len(_store) > MAX_ENTRIES:
                _store.popitem(last=False)
    return value


def stats() -> dict[str, int]:
    with _lock:
        return {"version": _version, "entries": len(_store)}
