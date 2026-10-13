"""One reproduced bug and two characterization tests around it.

REPRODUCED BUG
`_owner_of` indexed the first/last letter of a person's name without checking the token was
non-empty. A JSON-LD `Person` whose name tokenises to nothing usable ("!! ??", "1 2") raised
`IndexError: string index out of range`, which escaped `connector.fetch()` and aborted the whole
run — runs 75, 111 and 225 all ended that way, so the companies queued behind them were never
crawled. `test_owner_of_survives_a_name_with_no_latin_letters` fails on the original code.

CHARACTERIZATION (not a bug fix)
While investigating I suspected the crash also rolled back records already ingested, because
`db.session()` rolls back when an exception escapes it and `COMMIT_EVERY = 25` means a short run
reaches no commit boundary (run 225 reported `found=17`). The two tests below were written to
prove that. They pass on the unmodified code, so the suspicion was wrong: the savepoint releases
in `execute()` make each record durable regardless. They are kept as characterization tests to
pin that behaviour down, and this note exists so nobody later "fixes" a rollback bug that does
not exist.
"""

from __future__ import annotations

from sqlalchemy import func, select

from prospector import discovery
from prospector.connectors.website import _owner_of
from prospector.models import Company, Run
from prospector.resolve import CompanyRecord, PersonRecord


def test_owner_of_survives_a_name_with_no_latin_letters():
    # a JSON-LD person whose name tokenises to nothing usable must be skipped, not crash the run
    assert _owner_of("someone@acme.com", [PersonRecord("!! ??", "CEO")]) is None
    assert _owner_of("someone@acme.com", [PersonRecord("1 2", "Director")]) is None
    assert _owner_of("someone@acme.com", [PersonRecord(",., ;.", "Partner")]) is None
    # normal names still match, and a bad name never hides a good one
    good = PersonRecord("Jane Doe", "CEO")
    assert _owner_of("jane.doe@acme.com", [good, PersonRecord("!! ??", "CEO")]) is good
    assert _owner_of("jdoe@acme.com", [good]) is good


def test_records_survive_a_connector_crash(db, settings, monkeypatch):
    with db.session() as s:
        run = Run(source="probe-source", params={})
        s.add(run)
        s.flush()
        run_id = run.id

    class ExplodingConnector:
        needs_key = None
        maintenance = False
        needs_db = False

        def __init__(self, http, api_key=None):
            self.http = http

        def fetch(self, params):
            yield CompanyRecord(source="probe-source", url=None, name="Kept Co")
            raise RuntimeError("connector exploded")

    monkeypatch.setitem(discovery.CONNECTORS, "probe-source", ExplodingConnector)
    monkeypatch.setattr(discovery, "chain_enrichment", lambda *a, **k: 0)

    discovery.execute(db, run_id, settings, http=object())

    with db.session() as s:
        assert s.scalar(select(func.count()).where(Company.name == "Kept Co")) == 1
        run_after = s.get(Run, run_id)
        assert run_after.status == "failed"
        assert "connector exploded" in (run_after.message or "")


def test_records_beyond_the_last_commit_boundary_survive(db, settings, monkeypatch):
    """Cross a COMMIT_EVERY boundary and crash short of the next one; nothing may be lost."""
    with db.session() as s:
        run = Run(source="probe-source", params={})
        s.add(run)
        s.flush()
        run_id = run.id

    total = discovery.COMMIT_EVERY + 7

    class ExplodingConnector:
        needs_key = None
        maintenance = False
        needs_db = False

        def __init__(self, http, api_key=None):
            self.http = http

        def fetch(self, params):
            for n in range(total):
                yield CompanyRecord(source="probe-source", url=None, name=f"Batch Co {n:03d}")
            raise RuntimeError("connector exploded")

    monkeypatch.setitem(discovery.CONNECTORS, "probe-source", ExplodingConnector)
    monkeypatch.setattr(discovery, "chain_enrichment", lambda *a, **k: 0)

    discovery.execute(db, run_id, settings, http=object())

    with db.session() as s:
        landed = s.scalar(select(func.count()).where(Company.name.like("Batch Co %")))
        assert landed == total, f"expected all {total} records kept, only {landed} landed"
        assert s.get(Run, run_id).status == "failed"
