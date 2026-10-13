"""A database created before the CRM tables existed must converge itself when it is opened.

data/prospector.db shipped with the 18 tables of the original schema; Evidence, Automation,
DailyMetric, Exclusion, Task and Note were added later, and the server that had been running
since before that edit re-ran the bootstrap only against the 18 tables it had in memory. These
tests pin the convergence to opening the database, so no entry point can skip the migration.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect

from prospector.db import Database
from prospector.models import Base

# The six tables that were missing from the on-disk database.
NEW_TABLES = ("automations", "daily_metrics", "evidence", "exclusions", "notes", "tasks")


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_opening_a_legacy_database_creates_the_missing_tables(tmp_path) -> None:
    """The pre-CRM 18-table schema must migrate to Base.metadata when a Database is opened.

    Convergence cannot depend on callers remembering ``create_all()``: constructing the
    Database is the one step every entry point (serve, worker, create-user, tests) shares,
    so that is where the schema gap must be closed.
    """
    url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    engine = create_engine(url)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in NEW_TABLES:
                table.create(conn)  # the schema as it existed before the six models were added
        conn.execute(Base.metadata.tables["meta"].insert().values(key="provenance", value="legacy"))
    engine.dispose()

    assert not (set(NEW_TABLES) & _tables(url))  # precondition: the gap is real

    Database(url)

    assert set(NEW_TABLES) <= _tables(url)
    # Existing tables and their rows survive the migration untouched.
    engine = create_engine(url)
    try:
        rows = [tuple(r) for r in engine.connect().execute(Base.metadata.tables["meta"].select())]
    finally:
        engine.dispose()
    assert rows == [("provenance", "legacy")]
