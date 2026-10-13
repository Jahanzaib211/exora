"""When is an empty crawl a result, and when is it a fault?

`WebsiteConnector.fetch` used to raise whenever no site returned a page, whatever went wrong. The
crawler exiting cleanly with a closing summary means it ran and every target was unreachable (dead
DNS, refused connection, 429) - a property of the targets. Treating that as a fault marked every
scheduled slot red for as long as the queue held nothing but unreachable hosts, and because only a
successful crawl stamps `last_crawled_at`, the same hosts were picked again immediately: 111 of
252 website runs failed this way.

A crawler that never wrote its summary, or exited non-zero, is still a fault.
"""

from __future__ import annotations

import gzip
import json
from types import SimpleNamespace

import pytest

from prospector import discovery
from prospector.connectors import website as website_mod
from prospector.connectors.website import WebsiteConnector
from prospector.models import Run

SITE_IDS = [11, 22, 33]


def _payload(*lines: dict) -> bytes:
    return gzip.compress(("\n".join(json.dumps(line) for line in lines) + "\n").encode())


def _connector(db, monkeypatch, stdout: bytes, returncode: int = 0) -> WebsiteConnector:
    """A connector wired to a canned crawler reply, with the network and database out of the way."""
    monkeypatch.setattr(
        WebsiteConnector,
        "targets",
        lambda self, params: [{"company_id": i, "website": f"https://site{i}.example"} for i in SITE_IDS],
    )
    monkeypatch.setattr(
        WebsiteConnector, "_names", lambda self, ids: {i: (f"Company {i}", f"site{i}.example") for i in ids}
    )
    monkeypatch.setattr(
        website_mod.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout=stdout, stderr=b"all hosts unreachable", returncode=returncode),
    )
    return WebsiteConnector(None, db=db)


def test_completed_crawl_with_no_pages_is_a_result_not_a_fault(db, monkeypatch):
    out = _payload({"summary": {"pages": 0, "errors": 6, "sitemaps": 0}})
    conn = _connector(db, monkeypatch, out)

    assert list(conn.fetch({"limit": len(SITE_IDS)})) == []

    assert conn.fetch_note and conn.fetch_note.startswith(f"0 of {len(SITE_IDS)} sites returned a page")
    # Nothing may be stamped as crawled: these hosts are still worth trying next time.
    assert conn.crawled_ids == []


def test_a_crawl_that_never_wrote_its_summary_still_fails(db, monkeypatch):
    conn = _connector(db, monkeypatch, b"")
    with pytest.raises(RuntimeError, match="produced no pages"):
        list(conn.fetch({"limit": len(SITE_IDS)}))
    assert conn.fetch_note is None


def test_a_crashing_crawler_still_fails_even_with_a_summary(db, monkeypatch):
    out = _payload({"summary": {"pages": 0, "errors": 6, "sitemaps": 0}})
    conn = _connector(db, monkeypatch, out, returncode=3)
    with pytest.raises(RuntimeError, match="crawler exited 3"):
        list(conn.fetch({"limit": len(SITE_IDS)}))


def _make_run(db) -> int:
    with db.session() as s:
        run = Run(source="probe-source", params={})
        s.add(run)
        s.flush()
        return run.id


def test_a_run_that_retrieved_nothing_completes_and_says_why(db, settings, monkeypatch):
    note = "0 of 16 sites returned a page (16 requests failed: DNS, refused or rate-limited)"

    class SilentConnector:
        needs_key = None
        maintenance = False
        needs_db = False

        def __init__(self, http, api_key=None):
            self.http = http
            self.fetch_note = note

        def fetch(self, params):
            return iter(())

    monkeypatch.setitem(discovery.CONNECTORS, "probe-source", SilentConnector)
    monkeypatch.setattr(discovery, "chain_enrichment", lambda *a, **k: 0)

    run_id = _make_run(db)
    discovery.execute(db, run_id, settings, http=object())

    with db.session() as s:
        after = s.get(Run, run_id)
    assert after.status == "done"
    assert note in (after.message or "")


def test_a_clean_run_without_any_note_is_left_alone(db, settings, monkeypatch):
    class PlainConnector:
        needs_key = None
        maintenance = False
        needs_db = False

        def __init__(self, http, api_key=None):
            self.http = http

        def fetch(self, params):
            return iter(())

    monkeypatch.setitem(discovery.CONNECTORS, "probe-source", PlainConnector)
    monkeypatch.setattr(discovery, "chain_enrichment", lambda *a, **k: 0)

    run_id = _make_run(db)
    discovery.execute(db, run_id, settings, http=object())

    with db.session() as s:
        after = s.get(Run, run_id)
    assert after.status == "done"
    assert after.message is None  # no note, no invented text
