from __future__ import annotations

import contextlib
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Audit, Base

# How long a starting process waits for another process's schema migration before giving up.
# Kept in step with the SQLite busy_timeout below.
_MIGRATION_LOCK_TIMEOUT_S = 30.0

# Statements that change the database, and so must run inside a transaction.
_WRITE_SQL = re.compile(r"^(?:INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|UPSERT|REINDEX|VACUUM)\b", re.I)
# A `WITH` statement only writes when it carries DML. Treated separately so that a read CTE
# does not open a transaction (and therefore does not take the write lock).
_CTE_DML = re.compile(r"\b(?:INSERT|UPDATE|DELETE|REPLACE)\b", re.I)
# `Session.begin_nested()` opens and closes a SAVEPOINT. A SAVEPOINT issued outside a transaction
# starts one - deferred - so it is a real transaction boundary and needs the lock up front too.
_SAVEPOINT_OPEN = re.compile(r"^SAVEPOINT\b", re.I)
_SAVEPOINT_CLOSE = re.compile(r"^RELEASE\b", re.I)
# Per-Connection bookkeeping (kept in `Connection.info`, i.e. next to the connection it describes,
# never in a module global): how many savepoints are open, and whether the transaction underneath
# them was opened here rather than by an enclosing write.
_SAVEPOINT_DEPTH = "prospector_savepoint_depth"
_SAVEPOINT_OWNED = "prospector_savepoint_owned"


def _is_write_sql(sql: str) -> bool:
    """True when *sql* mutates the database and therefore needs an open transaction."""
    stripped = sql.lstrip()
    if _WRITE_SQL.match(stripped):
        return True
    return stripped[:5].upper() == "WITH " and bool(_CTE_DML.search(stripped))


def normalize_db_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


class Database:
    def __init__(self, url: str) -> None:
        url = normalize_db_url(url)
        self._sqlite_file: Path | None = None
        if url.startswith("sqlite:///"):
            # Keep the on-disk path so create_all() can place its migration lock next to the
            # database. A relative URL stays relative to the process cwd, which is what SQLAlchemy
            # does too.
            raw = url[len("sqlite:///") :]
            self._sqlite_file = Path(raw)
            self._sqlite_file.parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_conn, _):
                # Autocommit at the driver level, so nothing starts a transaction implicitly:
                # _open_write_txn below opens each one deliberately. pysqlite's own implicit
                # BEGIN is DEFERRED and fires halfway through a statement sequence, so two
                # connections that both hold read locks deadlock the moment both try to upgrade
                # to a write lock - SQLite answers SQLITE_BUSY immediately and busy_timeout never
                # applies. That was the recurring "database is locked" storm under two workers.
                dbapi_conn.isolation_level = None
                dbapi_conn.execute("PRAGMA foreign_keys=ON")  # SQLite ignores foreign keys unless asked
                dbapi_conn.execute("PRAGMA journal_mode=WAL")  # readers never block the worker's writes
                dbapi_conn.execute("PRAGMA busy_timeout=30000")
                # A writer that finds the database busy retries inside SQLite, but a WAL *checkpoint*
                # cannot wait for busy_timeout at all - it fails outright. This is what turned a
                # momentary write contention during startup into a dead process.
                dbapi_conn.execute("PRAGMA wal_autocheckpoint=1000")

            @event.listens_for(self.engine, "before_cursor_execute")
            def _open_write_txn(conn, cursor, statement, parameters, context, executemany):
                """Open the DBAPI transaction at the first write rather than at the first statement.

                Two failure modes pin down where the BEGIN has to go, and they rule out both of
                the obvious positions:

                * **At session start.** With `BEGIN` (deferred) the session's opening SELECT takes
                  a WAL read snapshot. Any other connection that commits before our first write
                  makes that snapshot stale, and SQLite answers with `SQLITE_BUSY_SNAPSHOT`
                  (517) *immediately* - the busy handler is not consulted at all, so the 30s
                  `busy_timeout` never comes into play. `verify_many` is exactly that shape:
                  read the batch, run multi-second DNS/RDAP lookups (during which a probe
                  connection commits), then flush the QualityCheck rows. Every writer that
                  touches the database while a reader idles loses its write.
                  `BEGIN IMMEDIATE` avoids the stale snapshot by taking the write lock up front,
                  but then a read-only GET holds the single writer slot for its whole duration -
                  the very stall the write-lock tests exist to prevent.

                * **Never** (what the code did before). `isolation_level = None` plus a pysqlite
                  `do_begin` that emits nothing means *no* BEGIN is ever issued, so every
                  statement autocommits. `Session.rollback()` still returns happily - it just has
                  nothing to undo. A handler that raised HTTPException halfway through a create
                  committed its half-finished row, and the retry then failed 409 "already
                  listed". Rollback was silently broken.

                Opening on the first write gets both right: reads stay in autocommit (nothing to
                pin, nothing held across a network lookup), the snapshot for the write is taken at
                that instant so it cannot be stale, and everything from the first write to
                `Session.commit()` is atomic - which is what rollback needs.

                `BEGIN IMMEDIATE` rather than `BEGIN` because this statement is already a write,
                so the lock is taken unconditionally; taking it here also means no read-then-write
                upgrade can fail.

                Whether a transaction is already open is read off the connection rather than
                tracked here, because `discovery.execute` opens one per record with
                `s.begin_nested()` - a `SAVEPOINT` outside a transaction starts one, and SQLite
                commits it on `RELEASE`. So the transaction is opened and closed several times
                between one `Session.begin()` and the next, and any bookkeeping kept alongside
                SQLAlchemy's logical transaction would disagree with reality: the flag said "open"
                while SQLite said otherwise, and the second `BEGIN IMMEDIATE` raised
                "cannot start a transaction within a transaction". `Connection.in_transaction` is
                `not sqlite3_get_autocommit()`, i.e. exactly what SQLite itself believes.

                **The savepoint case is the one that reading `in_transaction` does not settle.**
                A `SAVEPOINT` opens a *deferred* transaction, so the guard below then skips the
                `BEGIN IMMEDIATE` and the record runs the exact stale-snapshot shape described
                above: `upsert_company`'s SELECT pins a snapshot, the other worker commits, and
                the autoflush UPDATE fails with 517 - instantly, unretired. That is the residual
                "database is locked" traffic (556 logged lines over three days, every one from
                `discovery-worker`), and `busy_timeout` was never going to fix it. So `SAVEPOINT`
                counts as a statement that needs the lock: `BEGIN IMMEDIATE` goes down *first*,
                and `_release_savepoint` then commits when the outermost savepoint is released -
                which is what SQLite did anyway when the savepoint started the transaction, and
                what keeps the write lock free between records
                (tests/test_discovery_lock.py).
                """
                in_txn = cursor.connection.in_transaction
                stmt = statement.lstrip()
                if not in_txn:
                    # A ROLLBACK/COMMIT emitted behind our back (or a pooled connection handed back
                    # mid-transaction) must not leave a stale depth to be decremented later.
                    conn.info.pop(_SAVEPOINT_DEPTH, None)
                if _SAVEPOINT_OPEN.match(stmt):
                    depth = int(conn.info.get(_SAVEPOINT_DEPTH, 0)) + 1
                    conn.info[_SAVEPOINT_DEPTH] = depth
                    if depth == 1:
                        if not in_txn:
                            # Issued straight on the DBAPI cursor: SQLAlchemy must not see this as
                            # one of its own statements, and it must land before the savepoint.
                            cursor.execute("BEGIN IMMEDIATE")
                            conn.info[_SAVEPOINT_OWNED] = True
                        else:
                            # An enclosing write already holds the lock; it commits, not us.
                            conn.info[_SAVEPOINT_OWNED] = False
                elif _is_write_sql(stmt) and not in_txn:
                    cursor.execute("BEGIN IMMEDIATE")
                return statement, parameters

            @event.listens_for(self.engine, "after_cursor_execute")
            def _release_savepoint(conn, cursor, statement, parameters, context, executemany):
                """Commit the transaction a released savepoint was opened for.

                With `BEGIN IMMEDIATE` in place, `RELEASE` only pops the savepoint - SQLite no
                longer commits, because the transaction was started before the savepoint rather
                than by it. Leaving it open would hold the write lock across the next
                `connector.fetch()` network call, which is precisely what
                tests/test_discovery_lock.py forbids and what `busy_timeout` turns into lost
                writes elsewhere. So the lock is handed back the moment the outermost savepoint
                goes, restoring the per-record boundary SQLite used to give us for free.
                """
                if not _SAVEPOINT_CLOSE.match(statement.lstrip()):
                    return
                depth = max(int(conn.info.get(_SAVEPOINT_DEPTH, 0)) - 1, 0)
                conn.info[_SAVEPOINT_DEPTH] = depth
                if depth == 0 and conn.info.pop(_SAVEPOINT_OWNED, False) and cursor.connection.in_transaction:
                    cursor.execute("COMMIT")
        else:
            self.engine = create_engine(url, pool_pre_ping=True)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)
        # Schema convergence belongs here rather than at the call sites: constructing a Database is the one
        # step every entry point (serve, worker, create-user, tests) shares, so no path can open the database
        # before migrating it. Safe to repeat because create_all only ever adds tables and columns; without
        # this, a schema bootstrapped from older models (the on-disk DB kept 18 tables while Base.metadata
        # declared 24) never converges unless every caller remembers to migrate.
        self.create_all()

    def create_all(self) -> None:
        """
        Bring the schema up to date, safe to run concurrently.

        Every entry point constructs a `Database`, and the documented deployment runs `app`
        and `worker` as separate processes against one SQLite file. Both therefore reach
        `create_all` at the same moment. `BEGIN IMMEDIATE` plus `busy_timeout` only makes them
        queue politely — it does not stop both from running the DDL, and the loser's
        `ALTER TABLE ... ADD COLUMN` fails with `OperationalError: database is locked`,
        which propagates out of `__init__` and takes the whole process down before it ever
        binds a port. That is exactly how the app became unreachable.

        An exclusive advisory lock around the migration makes it single-writer. The loser
        waits, then finds the work already done and proceeds. `timeout` matches the SQLite
        busy_timeout so a genuine long migration still surfaces as an error rather than
        hanging forever.
        """
        if self.engine.dialect.name != "sqlite":
            Base.metadata.create_all(self.engine)
            self.add_missing_columns()
            return

        lock_path = self._sqlite_lock_path()
        deadline = time.monotonic() + _MIGRATION_LOCK_TIMEOUT_S
        acquired = False
        while True:
            try:
                # Create the lock file if needed; fail immediately if another process holds it.
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, str(os.getpid()).encode())
                finally:
                    os.close(fd)
                acquired = True
                break
            except FileExistsError:
                if self._stale_lock(lock_path):
                    # A previous process died mid-migration and left the file behind.
                    with contextlib.suppress(OSError):
                        os.unlink(lock_path)
                    continue
                if time.monotonic() >= deadline:
                    # RuntimeError, not OperationalError: SQLAlchemy's OperationalError
                    # subclasses DBAPIError, whose __init__ requires (statement, params, orig).
                    # Constructing it with only a message raises a confusing TypeError that masks
                    # the real cause.
                    raise RuntimeError(
                        f"schema migration is locked by another process ({lock_path}); "
                        f"waited {_MIGRATION_LOCK_TIMEOUT_S:.0f}s. If no other Exora process is "
                        f"running, delete that file and restart."
                    ) from None
                time.sleep(0.25)
        try:
            Base.metadata.create_all(self.engine)
            self.add_missing_columns()
        finally:
            if acquired:
                with contextlib.suppress(OSError):
                    os.unlink(lock_path)

    def _sqlite_lock_path(self) -> Path:
        raw = self._sqlite_file
        assert raw is not None  # noqa: S101 - only called on the sqlite branch
        return raw.with_name(raw.name + "-migrate.lock")

    def _stale_lock(self, lock_path: Path) -> bool:
        """True when the lock file is older than the timeout, i.e. its owner never came back."""
        try:
            age = time.time() - lock_path.stat().st_mtime
        except OSError:
            return True
        return age > _MIGRATION_LOCK_TIMEOUT_S * 2

    def add_missing_columns(self) -> list[str]:
        """Additive schema migration: new nullable columns on existing tables are added in place.

        Enough for this app's evolution (columns are only ever added). Renames or type changes would need a
        real migration tool (Alembic) and are deliberately not attempted here.
        """
        added = []
        insp = inspect(self.engine)
        existing_tables = set(insp.get_table_names())
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if table.name not in existing_tables:
                    continue
                have = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name in have:
                        continue
                    ddl = col.type.compile(dialect=self.engine.dialect)
                    default = ""
                    if not col.nullable:
                        # NOT NULL columns need a literal default for existing rows (Boolean/Integer defaults only)
                        value = getattr(col.default, "arg", None)
                        if isinstance(value, bool | int):
                            default = f" NOT NULL DEFAULT {int(value)}"
                        else:
                            continue  # cannot add safely; left for a real migration
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}{default}'))
                    added.append(f"{table.name}.{col.name}")
                for index in table.indexes:
                    index.create(bind=conn, checkfirst=True)
        return added

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


def audit(s: Session, action: str, user_id: int | None = None, target: str | None = None, **detail: Any) -> None:
    s.add(Audit(action=action, user_id=user_id, target=target, detail=detail or None))
