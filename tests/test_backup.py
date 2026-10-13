"""Tests for scripts/backup.py: the WAL-safe snapshot path, URL parsing and pruning."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup.py"


def _load():
    """Import scripts/backup.py by path (it is not a package module)."""
    spec = importlib.util.spec_from_file_location("backup_script", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backup_script"] = mod
    spec.loader.exec_module(mod)
    return mod


backup_mod = _load()


@pytest.fixture()
def wal_db(tmp_path: Path):
    """A WAL-mode database whose recent commits are still in the -wal file, i.e. the case a naive file copy
    misses. Returns (db_path, keepalive): the writer connection must stay open, because closing the last
    connection makes SQLite checkpoint the WAL into the main file and defeat the point of the fixture."""
    db = tmp_path / "live.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE company (id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(500):
        conn.execute("INSERT INTO company (name) VALUES (?)", (f"Co {i}",))
    conn.commit()
    # WAL_AUTOCHECKPOINT is off by default, so an open connection leaves the commits in the -wal file.
    assert (tmp_path / "live.db-wal").exists(), "fixture must leave data in the WAL"
    yield db, conn
    conn.close()


def test_url_parsing_absolute_and_relative():
    # POSIX absolute: four slashes leave an absolute path after the "sqlite:///" prefix.
    if os.name != "nt":
        assert backup_mod.sqlite_path("sqlite:////abs/path.db") == Path("/abs/path.db")
    # Windows absolute: SQLAlchemy drops the leading slash and keeps the drive letter.
    if os.name == "nt":
        assert backup_mod.sqlite_path("sqlite:///C:/data/x.db") == Path("C:/data/x.db")
    # Relative paths resolve against the project root, not the caller's cwd.
    rel = backup_mod.sqlite_path("sqlite:///data/x.db")
    assert rel.name == "x.db" and rel.parent.name == "data"
    assert rel.is_absolute(), "relative URLs resolve against the project root"


def test_url_parsing_rejects_postgres():
    with pytest.raises(SystemExit) as exc:
        backup_mod.sqlite_path("postgresql+psycopg://user:pw@host/db")
    assert "pg_dump" in str(exc.value)


def test_backup_captures_wal_contents(tmp_path: Path, wal_db):
    """The snapshot must include rows that live only in -wal, not just the main file."""
    db, _keepalive = wal_db
    out = tmp_path / "snap.db"
    backup_mod.backup(db, out)

    assert out.exists()
    conn = sqlite3.connect(out)
    try:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT count(*) FROM company").fetchone()[0] == 500
    finally:
        conn.close()


def test_backup_leaves_source_untouched(wal_db, tmp_path: Path):
    db, keepalive = wal_db
    before = db.read_bytes()
    backup_mod.backup(db, tmp_path / "snap.db")
    assert db.read_bytes() == before
    # Source still usable, still in WAL mode, and the writer connection still holds it.
    assert keepalive.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert keepalive.execute("SELECT count(*) FROM company").fetchone()[0] == 500


def test_backup_refuses_to_overwrite(tmp_path: Path, wal_db):
    db, _keepalive = wal_db
    out = tmp_path / "snap.db"
    backup_mod.backup(db, out)
    with pytest.raises(SystemExit) as exc:
        backup_mod.backup(db, out)
    assert "overwrite" in str(exc.value)


def test_backup_missing_source(tmp_path: Path):
    with pytest.raises(SystemExit) as exc:
        backup_mod.backup(tmp_path / "nope.db", tmp_path / "out.db")
    assert "not found" in str(exc.value)


def test_backup_rejects_corrupt_source(tmp_path: Path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite database at all")
    with pytest.raises(sqlite3.DatabaseError):
        backup_mod.backup(bad, tmp_path / "out.db")


def test_prune_keeps_newest(tmp_path: Path):
    names = [f"prospector-2026100{i}-000000.db" for i in range(5)]
    for n in names:
        (tmp_path / n).write_bytes(b"x")

    removed = backup_mod.prune(tmp_path, keep=2)

    remaining = sorted(p.name for p in tmp_path.glob("prospector-*.db"))
    assert remaining == sorted(names[-2:]), "newest two are by filename order"
    assert sorted(p.name for p in removed) == sorted(names[:-2])


def test_prune_rejects_keep_below_one(tmp_path: Path):
    with pytest.raises(SystemExit):
        backup_mod.prune(tmp_path, keep=0)
