from datetime import timedelta

from prospector.models import Automation, DailyMetric, Run, utcnow
from prospector.scheduler import BUILTINS, seed_automations, snapshot, tick, trigger


def make(db, **kw) -> int:
    defaults = dict(
        name="Grow test", source="osm", params={"segment": "Law", "country": "GB"}, rotate_field="city",
        rotate_values=["Leeds", "York"], cadence_minutes=60, enabled=True, next_run_at=utcnow() - timedelta(minutes=1),
    )  # fmt: skip
    with db.session() as s:
        a = Automation(**(defaults | kw))
        s.add(a)
        s.flush()
        return a.id


def test_seed_is_idempotent(db):
    assert seed_automations(db) == len(BUILTINS)
    assert seed_automations(db) == 0


def test_tick_rotates_and_never_stacks_runs(db):
    aid = make(db)
    first = tick(db)
    assert len(first) == 1
    with db.session() as s:
        run = s.get(Run, first[0])
        assert run.params["city"] == "Leeds" and run.automation_id == aid
        a = s.get(Automation, aid)
        assert a.rotate_index == 1 and a.next_run_at > utcnow()
        a.next_run_at = utcnow() - timedelta(minutes=1)  # due again while the first run is still queued
    assert tick(db) == []  # no second run while one is queued
    with db.session() as s:
        s.get(Run, first[0]).status = "done"
        s.get(Automation, aid).next_run_at = utcnow() - timedelta(minutes=1)
    second = tick(db)
    with db.session() as s:
        assert s.get(Run, second[0]).params["city"] == "York"
        assert s.get(Automation, aid).rotate_index == 0  # wrapped around


def test_disabled_and_broken_automations(db):
    make(db, name="off", enabled=False)
    assert tick(db) == []
    broken = make(db, name="broken", params={"segment": "Nope"})
    assert trigger(db, broken) is None
    with db.session() as s:
        assert s.get(Automation, broken).enabled is False  # stops retrying a bad configuration


def test_snapshot_upserts_today(db):
    snapshot(db)
    snapshot(db)
    with db.session() as s:
        assert s.query(DailyMetric).count() == 1
