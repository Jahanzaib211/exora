"""Consistent backups of the Exora SQLite database.

A plain `copy` of prospector.db is unsafe while the engine is running: SQLite is in WAL mode, so recent commits
live in `-wal` until a checkpoint, and a copy taken mid-write can be torn. `VACUUM INTO` asks SQLite for a
transactionally consistent snapshot instead - it works on a live database, produces a defragmented copy, and
needs no downtime.

    python scripts/backup.py                      # -> data/backups/prospector-<timestamp>.db
    python scripts/backup.py --out /tmp/x.db
    python scripts/backup.py --keep 14             # prune, keeping the newest 14

Postgres deployments are dumped by `pg_dump` instead (see docs/DEPLOYMENT.md); this script refuses a
non-SQLite URL rather than silently producing a corrupt file.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from prospector.settings import load_settings  # noqa: E402


def sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise SystemExit(
            f"backup.py only handles SQLite (got {database_url.split(':', 1)[0]}). "
            "For Postgres use: pg_dump --format=custom --file <out>.dump <url>"
        )
    raw = database_url[len(prefix) :]
    # sqlite:////abs/path keeps three slashes + absolute path; sqlite:///rel/path has none after the prefix.
    path = Path(raw[1:] if raw.startswith("/") and not raw.startswith("//") else raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


def backup(src: Path, out: Path, timeout_s: float = 300.0) -> Path:
    if not src.is_file():
        raise SystemExit(f"database not found: {src}")
    out.parent.mkdir(parents=True, exist_ok=True)
    # VACUUM INTO cannot overwrite an existing file.
    if out.exists():
        raise SystemExit(f"refusing to overwrite an existing backup: {out}")
    conn = sqlite3.connect(src, timeout=timeout_s)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        # Fails loudly if the source is corrupt, instead of writing a broken snapshot.
        conn.execute("PRAGMA quick_check").fetchone()
        conn.execute("VACUUM INTO ?", (str(out),))
    finally:
        conn.close()
    return out


def prune(directory: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` backups. Returns the paths removed."""
    if keep < 1:
        raise SystemExit("--keep must be at least 1")
    backups = sorted(directory.glob("prospector-*.db"), key=lambda p: p.name, reverse=True)
    doomed = backups[keep:]
    for path in doomed:
        path.unlink()
    return doomed


def main() -> None:
    ap = argparse.ArgumentParser(description="Consistent SQLite backup of the Exora database.")
    ap.add_argument("--out", type=Path, help="destination file (default: data/backups/prospector-<ts>.db)")
    ap.add_argument("--keep", type=int, help="after backing up, keep only the newest N backups")
    args = ap.parse_args()

    settings = load_settings()
    src = sqlite_path(settings.database_url)
    out = args.out or (ROOT / "data" / "backups" / f"prospector-{datetime.now(UTC):%Y%m%d-%H%M%S}.db")

    made = backup(src, out)
    size = made.stat().st_size
    print(f"backup ok: {made} ({size:,} bytes)")

    if args.keep:
        for path in prune(made.parent, args.keep):
            print(f"pruned: {path}")


if __name__ == "__main__":
    main()
