"""Discovery runs: a durable queue in the `runs` table and the workers that execute it.

Postgres: runs are claimed with FOR UPDATE SKIP LOCKED, so workers on several machines can share the queue.
SQLite (laptop): the claim is an atomic UPDATE ... WHERE status='queued', so several threads never take the same
run. Each run commits in batches, re-scores what it touched, and (for discovery sources) queues a website crawl
for new companies, so every new lead is enriched automatically.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from . import cache
from .connectors import CONNECTORS, PoliteClient
from .db import Database
from .models import Company, Run, utcnow
from .resolve import ExcludedRecord, upsert_company
from .seed import set_meta
from .verify import DnsCache, verify_company

log = logging.getLogger(__name__)
COMMIT_EVERY = 25
ENRICH_BATCH = 50
MAX_IDS = 500
STALE_RUN_HOURS = 3
NO_CHAIN = {"website", "verify", "jobs", "news", "outreach"}  # these never trigger another crawl


def commit_with_retry(s, attempts: int = 4) -> None:
    """Commit, retrying transient SQLite lock contention.

    BEGIN IMMEDIATE (see db.py) makes writers queue rather than deadlock, so a locked database here
    means some writer exceeded the 30 s busy timeout - almost always a long crawl batch on a laptop
    that was asleep or throttled. A short back-off recovers those instead of failing the record.
    """
    for attempt in range(attempts):
        try:
            s.commit()
            return
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            s.rollback()
            time.sleep(0.5 * (attempt + 1))


class RunError(ValueError):
    pass


def validate_params(source: str, params: dict[str, Any]) -> dict[str, Any]:
    cls = CONNECTORS.get(source)
    if cls is None:
        raise RunError(f"Unknown source: {source}")
    clean: dict[str, Any] = {}
    for p in cls.params:
        v = params.get(p.name, p.default)
        if v in (None, "", []) and p.required:
            raise RunError(f"{p.label} is required")
        if v in (None, "", []):
            continue
        if p.kind == "int":
            try:
                v = max(0, min(int(v), 5000))
            except (TypeError, ValueError) as exc:
                raise RunError(f"{p.label} must be a number") from exc
        elif p.kind == "ids":
            raw = v if isinstance(v, list) else str(v).split(",")
            try:
                v = sorted({int(x) for x in raw if str(x).strip()})[:MAX_IDS]
            except (TypeError, ValueError) as exc:
                raise RunError(f"{p.label} must be a list of numbers") from exc
        elif p.kind == "select" and p.options and v not in p.options:
            raise RunError(f"{p.label} must be one of: {', '.join(p.options)}")
        elif p.kind == "textarea":
            v = str(v).strip()[:20000]
        else:
            v = str(v).strip()[:200]
        clean[p.name] = v
    return clean


def enqueue(
    db: Database, source: str, params: dict[str, Any], user_id: int | None, automation_id: int | None = None
) -> int:
    clean = validate_params(source, params)
    with db.session() as s:
        run = Run(source=source, params=clean, status="queued", created_by=user_id, automation_id=automation_id)
        s.add(run)
        s.flush()
        return run.id


def claim_next(db: Database) -> int | None:
    with db.session() as s:
        q = select(Run.id).where(Run.status == "queued").order_by(Run.created_at, Run.id).limit(1)
        if db.engine.dialect.name == "postgresql":
            q = q.with_for_update(skip_locked=True)
        run_id = s.scalar(q)
        if run_id is None:
            return None
        claimed = s.execute(
            update(Run).where(Run.id == run_id, Run.status == "queued").values(status="running", started_at=utcnow())
        ).rowcount
        return run_id if claimed else None


def _connector(db: Database, source: str, settings, http: PoliteClient):
    cls = CONNECTORS[source]
    kwargs: dict[str, Any] = {}
    if source == "seed-sheet":
        kwargs["root"] = Path(settings.docs_root)
        kwargs["default_path"] = settings.seed_csv
    if getattr(cls, "needs_db", False):
        kwargs["db"] = db
    if getattr(cls, "maintenance", False):
        kwargs["settings"] = settings
    api_key = getattr(settings, cls.needs_key, None) if cls.needs_key else None
    return cls(http, api_key, **kwargs)


def execute(db: Database, run_id: int, settings, http: PoliteClient | None = None) -> None:
    with db.session() as s:
        run = s.get(Run, run_id)
        source, params, user_id = run.source, dict(run.params or {}), run.created_by
    own_http = http is None
    http = http or PoliteClient()
    connector = _connector(db, source, settings, http)
    found = created = updated = errors = excluded = 0
    touched: set[int] = set()
    new_ids: set[int] = set()
    status, message = "done", None
    try:
        if getattr(connector, "maintenance", False):
            found, updated, message = connector.run_maintenance(params)
        else:
            with db.session() as s:
                for i, rec in enumerate(connector.fetch(params), start=1):
                    found += 1
                    try:
                        with s.begin_nested():  # one bad record must not lose the batch
                            company, was_new = upsert_company(s, rec, run_id)
                        touched.add(company.id)
                        if was_new:
                            new_ids.add(company.id)
                        created += was_new
                        updated += not was_new
                    except ExcludedRecord:
                        excluded += 1
                    except Exception as exc:
                        errors += 1
                        log.warning("run %s record %s failed: %s", run_id, rec.name, exc)
                    if i % COMMIT_EVERY == 0:
                        s.commit()
                        _progress(db, run_id, found, created, updated, errors)
                        cache.bump()  # long runs: let the UI see new records as they land
    except Exception as exc:
        status, message = "failed", f"{type(exc).__name__}: {exc}"[:1000]
        log.exception("run %s failed", run_id)
    finally:
        if own_http:
            http.close()
    # Everything from here to the terminal status write used to run outside the try/except. A failure
    # in mark_checked(), in the `last_crawled_at` update or in verify_many() escaped execute()
    # entirely, so the run row was never closed and stayed "running" until reap_stale_runs fired
    # three hours later — while the UI showed a job that had actually finished.
    try:
        if hasattr(connector, "mark_checked"):
            connector.mark_checked()
        crawled = getattr(connector, "crawled_ids", None)
        if crawled:
            with db.session() as s:
                s.execute(update(Company).where(Company.id.in_(crawled)).values(last_crawled_at=utcnow()))
            touched |= set(crawled)
        if touched:
            verify_many(db, sorted(touched))
    except Exception:
        # The run's own outcome is already decided; record the bookkeeping problem without
        # losing the status write below.
        log.exception("post-run bookkeeping failed for run %s", run_id)
        if status == "done":
            status = "failed"
            message = "; ".join(filter(None, [message, "post-run bookkeeping failed (see server log)"]))
    try:
        chained = 0
        if getattr(settings, "auto_enrich", True) and source not in NO_CHAIN and new_ids:
            chained = chain_enrichment(db, sorted(new_ids), user_id)
        if status == "done":
            notes = [message] if message else []
            # A connector that ran to completion but retrieved nothing says so here. Without it a
            # green run with `found=0` is indistinguishable from "everything was already current",
            # which is how the unreachable-host loop stayed invisible while it filled the log.
            fetch_note = getattr(connector, "fetch_note", None)
            if fetch_note:
                notes.append(str(fetch_note))
            if chained:
                notes.append(f"Queued website crawls for {chained} new companies")
            if excluded:
                notes.append(f"Skipped {excluded} excluded companies")
            message = "; ".join(notes) or None
        with db.session() as s:
            run = s.get(Run, run_id)
            run.status, run.message, run.finished_at = status, message, utcnow()
            run.found, run.created, run.updated, run.errors = found, created, updated, errors
    except Exception:
        # Last resort: a status write that fails must still be visible, not silently lost.
        log.exception("could not close run %s", run_id)
        raise
    cache.bump()


def chain_enrichment(db: Database, company_ids: list[int], user_id: int | None) -> int:
    """Queue website crawls for new companies that have a website. Returns how many were queued."""
    with db.session() as s:
        ids = list(
            s.scalars(
                select(Company.id).where(
                    Company.id.in_(company_ids), Company.website.is_not(None), Company.last_crawled_at.is_(None)
                )
            )
        )
    for i in range(0, len(ids), ENRICH_BATCH):
        enqueue(db, "website", {"company_ids": ids[i : i + ENRICH_BATCH], "recrawl_days": 0}, user_id)
    return len(ids)


def verify_many(db: Database, company_ids: list[int], dns_cache: DnsCache | None = None) -> None:
    """Re-score companies (DNS lookups are cached per call)."""
    cache = dns_cache or DnsCache()
    for i in range(0, len(company_ids), 50):
        with db.session() as s:
            batch = list(s.scalars(select(Company).where(Company.id.in_(company_ids[i : i + 50]))))
            for c in batch:
                try:
                    verify_company(s, c, cache)
                    # Commit per company, not per 50: this is what actually releases SQLite's write lock.
                    # Holding it across a whole batch meant company N+1's DNS ran while other writers
                    # (the second worker, _progress, API handlers) were already waiting, and they fail
                    # outright once busy_timeout expires.
                    s.commit()
                except Exception:
                    log.exception("verify failed for company %s", c.id)
                    s.rollback()


def _progress(db: Database, run_id: int, found: int, created: int, updated: int, errors: int) -> None:
    with db.session() as s:
        s.execute(
            update(Run).where(Run.id == run_id).values(found=found, created=created, updated=updated, errors=errors)
        )


def reap_stale_runs(db: Database, hours: int = STALE_RUN_HOURS) -> int:
    """A run 'running' for longer than any job can take was orphaned (crash, sleep): mark it failed."""
    cutoff = utcnow() - timedelta(hours=hours)
    with db.session() as s:
        return s.execute(
            update(Run)
            .where(Run.status == "running", Run.started_at < cutoff)
            .values(status="failed", finished_at=utcnow(), message="Stopped: no progress for 3 hours")
        ).rowcount


class Worker:
    """Background threads that drain the run queue. Started by `prospector serve` (or `prospector worker`)."""

    def __init__(self, db: Database, settings, poll_seconds: float = 3.0) -> None:
        self.db, self.settings, self.poll = db, settings, poll_seconds
        self.threads = max(1, int(getattr(settings, "worker_threads", 2)))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.started_at = utcnow()

    def recover(self) -> None:
        """At process start nothing can be running yet: runs left 'running' by a crash go back to the queue."""
        with self.db.session() as s:
            s.execute(update(Run).where(Run.status == "running").values(status="queued", started_at=None))

    def start(self) -> None:
        self.recover()
        for n in range(self.threads):
            t = threading.Thread(target=self._loop, name=f"discovery-worker-{n + 1}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=10)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                run_id = claim_next(self.db)
                if run_id is None:
                    self._stop.wait(self.poll)
                    continue
                execute(self.db, run_id, self.settings)
            except Exception:
                log.exception("worker loop error")
                self._stop.wait(self.poll)


def heartbeat(db: Database, name: str) -> None:
    with db.session() as s:
        set_meta(s, f"heartbeat:{name}", utcnow().isoformat(timespec="seconds"))
