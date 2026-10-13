"""Guard: a record inside `s.begin_nested()` must not lose its write to a concurrent commit.

`discovery.execute` opens one transaction per record with `s.begin_nested()`. A `SAVEPOINT`
issued outside a transaction starts a *deferred* one, so nothing holds the write lock while the
record's own statements run. The shape that follows is the one SQLite answers with
`SQLITE_BUSY_SNAPSHOT` (517):

    open savepoint -> SELECT (pins the WAL read snapshot) -> another connection commits
                   -> our write -> 517, immediately

The busy handler is never consulted for 517, so `PRAGMA busy_timeout=30000` cannot retry it and
nothing else retries either: the record is simply lost. This is the residual "database is locked"
traffic from `discovery-worker` that survived the earlier lazy-BEGIN fix (556 logged lines).

`db` now issues `BEGIN IMMEDIATE` before the savepoint, so the write lock is ours before the
SELECT ever runs and the snapshot cannot go stale. The second half of the test pins the other
half of the contract: the lock has to be free again as soon as the savepoint is released, or the
record's transaction would be held across the next `connector.fetch()` network call
(tests/test_discovery_lock.py).
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import text

PROBE = text("INSERT INTO lock_probe (v) VALUES (:v)")
PEER = "INSERT INTO lock_probe (v) VALUES (?)"


def _peer_insert(db_file: str, value: str) -> str:
    """Insert from an independent connection with a deliberately short busy timeout.

    Short on purpose: with the fix, this peer is the one that has to wait for our lock, and a
    30s wait would stall the test. Without the fix it commits instantly, which is exactly the
    commit that stales our snapshot - so the returned status is diagnostic either way.
    """
    conn = sqlite3.connect(db_file, timeout=0.3)
    conn.execute("PRAGMA busy_timeout=300")
    try:
        conn.execute(PEER, (value,))
        conn.commit()
    except sqlite3.OperationalError as exc:
        conn.rollback()
        return f"locked: {exc}"
    finally:
        conn.close()
    return "committed"


def test_record_write_survives_concurrent_commit(db, settings):
    db_file = str(db._sqlite_file)
    with db.engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS lock_probe (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)"))

    error: str | None = None
    peer = "not-run"
    try:
        with db.session() as s, s.begin_nested():  # discovery.execute's per-record boundary
            # The record's read: this pins the snapshot the write will be judged against.
            s.execute(text("SELECT count(*) FROM lock_probe"))
            # Somebody else commits while our record is still open.
            peer = _peer_insert(db_file, "peer")
            s.execute(PROBE, {"v": "record"})
    except sqlite3.OperationalError as exc:
        error = f"{exc} (sqlite_errorcode={getattr(exc, 'sqlite_errorcode', None)})"

    assert error is None, f"record write lost to a concurrent commit: {error}; peer was {peer}"
    assert peer in ("committed",) or peer.startswith("locked:"), peer

    # The lock must be free the instant the savepoint is released, or discovery would hold it
    # across the connector's next network call.
    after = _peer_insert(db_file, "after-release")
    assert after == "committed", f"write lock still held after RELEASE SAVEPOINT: {after}"

    with db.session() as s:
        rows = {r for (r,) in s.execute(text("SELECT v FROM lock_probe"))}
    assert "record" in rows, f"the record's write never landed: {sorted(rows)}"


def test_repeated_records_do_not_accumulate_a_held_lock(db, settings):
    """25 records (discovery's COMMIT_EVERY) must leave no transaction open behind them."""
    db_file = str(db._sqlite_file)
    with db.engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS lock_probe (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)"))

    with db.session() as s:
        for i in range(25):
            with s.begin_nested():
                s.execute(text("SELECT count(*) FROM lock_probe"))
                s.execute(PROBE, {"v": f"rec-{i}"})
        s.commit()  # discovery.py's COMMIT_EVERY flush

    assert _peer_insert(db_file, "final") == "committed"

    with db.session() as s:
        rows = [r for (r,) in s.execute(text("SELECT v FROM lock_probe WHERE v LIKE 'rec-%'"))]
    assert len(rows) == 25, f"expected 25 records, got {len(rows)}"


def test_enclosing_write_transaction_is_not_committed_early(db, settings):
    """A savepoint nested inside a real write transaction must not hand the lock back."""
    db_file = str(db._sqlite_file)
    with db.engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS lock_probe (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)"))

    with db.session() as s:
        s.execute(PROBE, {"v": "outer"})  # opens BEGIN IMMEDIATE -> we do not own this transaction
        with s.begin_nested():
            s.execute(PROBE, {"v": "inner"})
        # Still inside the outer transaction: an independent writer must not get in.
        assert _peer_insert(db_file, "blocked") != "committed", "outer transaction was committed by RELEASE"
        s.commit()

    assert _peer_insert(db_file, "final") == "committed"

    with db.session() as s:
        rows = {r for (r,) in s.execute(text("SELECT v FROM lock_probe"))}
    assert {"outer", "inner"} <= rows, sorted(rows)


def test_read_only_savepoint_does_not_pin_the_database(db, settings):
    """A nested block that writes nothing must still hand the lock straight back."""
    db_file = str(db._sqlite_file)
    with db.engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS lock_probe (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)"))

    with db.session() as s, s.begin_nested():
        s.execute(text("SELECT count(*) FROM lock_probe"))

    assert _peer_insert(db_file, "after-readonly") == "committed"
