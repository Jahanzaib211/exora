"""Regression: verification must never hold the SQLite write lock across a network lookup.

`verify_many` scores companies through `verify_company`, which resolves DNS (A/AAAA/MX/TXT) and looks up
domain age over RDAP. Those are network calls with multi-second timeouts. The original implementation
kept one session open for 50 companies and, inside `verify_company`, the `QualityCheck` loop's `SELECT`
triggered an autoflush that took the *write* lock -- after which the DMARC/TXT and RDAP lookups ran while
still holding it. One such batch outlives `busy_timeout` (30s), so every other writer (the second discovery
worker, `_progress`, and the FastAPI request handlers) then fails instantly with "database is locked".
Those writes are not retried: 252 such failures are in the live log, each losing a record or an audit row.

The invariant asserted here is simple: at the moment a DNS/RDAP lookup runs, an independent connection
must still be able to write.
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import select, text

from prospector import verify
from prospector.discovery import verify_many
from prospector.models import Company
from prospector.normalize import name_key


def _independent_write(db_file: str) -> dict[str, object]:
    """Try to write from a separate connection, with a short timeout so a held lock fails fast."""
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


def test_write_lock_free_during_every_dns_lookup(db, tmp_path, monkeypatch):
    db_file = (tmp_path / "p.db").as_posix()
    with db.engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS lock_probe (id INTEGER PRIMARY KEY AUTOINCREMENT)"))

    with db.session() as s:
        for i in range(3):
            name = f"Co {i}"
            s.add(
                Company(
                    name=name,
                    name_key=name_key(name),
                    domain=f"co{i}.example",
                    website=f"https://co{i}.example",
                )
            )
        s.flush()
        ids = list(s.scalars(select(Company.id)))

    probes: list[dict[str, object]] = []

    def fake_resolve(domain: str, rtype: str) -> list[str]:
        probes.append({"phase": f"DNS {rtype} {domain}", **_independent_write(db_file)})
        return []

    def fake_rdap(domain: str):
        probes.append({"phase": f"RDAP {domain}", **_independent_write(db_file)})
        return None

    monkeypatch.setattr(verify, "_resolve", fake_resolve)
    monkeypatch.setattr(verify, "_rdap_created", fake_rdap)

    verify_many(db, ids)

    assert probes, "expected DNS/RDAP lookups to happen during verify_many"
    held = [p for p in probes if not p["ok"]]
    assert not held, (
        f"{len(held)}/{len(probes)} network lookups ran while the write lock was held; "
        f"other writers would then get 'database is locked'. First offender: {held[0]['phase']} "
        f"-> {held[0].get('error')}"
    )

    # The scoring itself must still have happened: every company got quality-scored.
    with db.session() as s:
        scored = list(s.scalars(select(Company.quality_score).where(Company.id.in_(ids))))
    assert len(scored) == 3 and all(v is not None for v in scored), scored
