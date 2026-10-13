"""Guard: discovery must not hold the SQLite write lock across connector network I/O.

`connector.fetch()` yields records lazily, doing its network work *between* yields, so the write lock has
to be free again before the connector fetches the next record. Two worker threads plus the API all write to
the same database; a lock held past `busy_timeout` (30s) makes their writes fail outright with "database is
locked", and those failures are not retried, so records are silently lost.

This test drives `execute()` with a fake connector that, immediately after the first record has been
ingested, attempts a write from an independent connection. It must succeed.

Honest scope note: this guards the *current* structure. It does not reproduce the historical bug -- under
the older single-session-per-run shape, `begin_nested()`'s savepoint happened to release the lock anyway,
so this test passed on that code too. The bug that actually caused the 252 lost writes is in `verify_many`
and is covered by tests/test_write_lock.py, which does fail without its fix.
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import func, select, text

from prospector import discovery
from prospector.models import Company, Run
from prospector.resolve import CompanyRecord


def _independent_write(db_file: str) -> dict[str, object]:
    """Attempt a write from a separate connection with a short busy timeout.

    A short timeout keeps the test fast whether it passes or fails: if the discovery transaction still
    holds the write lock, this returns a "database is locked" error instead of hanging for 30 seconds.
    """
    conn = sqlite3.connect(db_file, timeout=0.3)
    try:
        conn.execute("PRAGMA busy_timeout=300")
        conn.execute("INSERT INTO lock_probe DEFAULT VALUES")
        conn.commit()
        return {"ok": True}
    except sqlite3.OperationalError as exc:
        conn.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()


def test_write_lock_released_between_records(db, settings, tmp_path, monkeypatch):
    db_file = (tmp_path / "p.db").as_posix()
    with db.engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS lock_probe (id INTEGER PRIMARY KEY AUTOINCREMENT)"))

    with db.session() as s:
        run = Run(source="probe-source", params={})
        s.add(run)
        s.flush()
        run_id = run.id

    probes: list[dict[str, object]] = []

    class FakeConnector:
        needs_key = None
        maintenance = False
        needs_db = False

        def __init__(self, http, api_key=None):
            self.http = http

        def fetch(self, params):
            # The first record is a brand-new company, so ingesting it must INSERT -> take the write lock.
            yield CompanyRecord(source="probe-source", url=None, name="First Co")
            # The loop has finished handling record 1. Is the lock already released?
            probes.append(_independent_write(db_file))
            yield CompanyRecord(source="probe-source", url=None, name="Second Co")

    monkeypatch.setitem(discovery.CONNECTORS, "probe-source", FakeConnector)
    monkeypatch.setattr(discovery, "chain_enrichment", lambda *a, **k: 0)

    discovery.execute(db, run_id, settings, http=object())

    assert probes, "fetch() was never consumed past the first record"
    assert probes[0]["ok"], f"write lock still held between records: {probes[0].get('error')}"

    with db.session() as s:
        landed = s.scalar(select(func.count()).where(Company.name.in_(["First Co", "Second Co"])))
    assert landed == 2, f"expected both records to be ingested, got {landed}"

    with db.session() as s:
        run_after = s.get(Run, run_id)
        assert run_after.status == "done", run_after.message
