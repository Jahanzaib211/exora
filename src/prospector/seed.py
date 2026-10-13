"""Sync the database from the plan markdown (09_30_Day_Execution_Plan_and_KPIs.md).

The markdown is the source of truth for content. Team state (task status/assignee/notes, event status/notes,
routine ticks, KPI entries, calls) is never touched. Rows that disappear from the markdown are archived,
not deleted, so their history survives.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .mdsource import PlanSpec, parse_plan
from .models import Event, FunnelStage, KpiDef, Meta, PlanTask, PlanWeek, RoutineItem, utcnow


def get_meta(s: Session, key: str) -> str | None:
    row = s.get(Meta, key)
    return row.value if row else None


def set_meta(s: Session, key: str, value: str | None) -> None:
    row = s.get(Meta, key) or Meta(key=key)
    row.value = value
    s.add(row)


def campaign_start(s: Session) -> date | None:
    v = get_meta(s, "campaign_start")
    return date.fromisoformat(v) if v else None


def sync_plan(s: Session, spec: PlanSpec) -> dict[str, int]:
    counts = {
        "weeks": 0,
        "tasks_new": 0,
        "tasks_updated": 0,
        "tasks_archived": 0,
        "kpis": 0,
        "funnel": 0,
        "routine": 0,
        "events": 0,
        "events_archived": 0,
    }
    # Weeks (id = position) and their tasks
    live_keys: set[str] = set()
    for i, w in enumerate(spec.weeks, start=1):
        week = s.get(PlanWeek, i) or PlanWeek(id=i)
        week.label, week.start, week.end, week.targets = w.label, w.start, w.end, w.targets
        week.focus = w.focus or week.focus or ""
        s.add(week)
        s.flush()
        counts["weeks"] += 1
        for n, t in enumerate(w.tasks):
            live_keys.add(t.key)
            task = s.scalar(select(PlanTask).where(PlanTask.key == t.key))
            if task is None:
                s.add(PlanTask(key=t.key, week_id=i, track=t.track, title=t.title, sort=n))
                counts["tasks_new"] += 1
            else:
                if (task.title, task.track, task.week_id, task.sort, task.archived) != (t.title, t.track, i, n, False):
                    counts["tasks_updated"] += 1
                task.title, task.track, task.week_id, task.sort, task.archived = t.title, t.track, i, n, False
    for task in s.scalars(select(PlanTask).where(PlanTask.archived.is_(False))):
        if task.key not in live_keys:
            task.archived = True
            counts["tasks_archived"] += 1
    # KPIs
    for n, k in enumerate(spec.kpis):
        row = s.get(KpiDef, k.key) or KpiDef(key=k.key)
        row.name, row.unit, row.direction, row.target, row.threshold = (
            k.name,
            k.unit,
            k.direction,
            k.target,
            k.threshold,
        )
        row.min_sample, row.action, row.source, row.sort = k.min_sample, k.action, k.source, n
        row.raw_target, row.raw_threshold = k.raw_target, k.raw_threshold
        s.add(row)
        counts["kpis"] += 1
    # Funnel
    for n, f in enumerate(spec.funnel):
        row = s.get(FunnelStage, f.key) or FunnelStage(key=f.key)
        row.name, row.assumption, row.source = f.name, f.assumption, f.source
        row.plan_low, row.plan_high, row.is_assumption, row.raw, row.sort = (
            f.plan_low,
            f.plan_high,
            f.is_assumption,
            f.raw,
            n,
        )
        s.add(row)
        counts["funnel"] += 1
    # Routine
    for r in spec.routine:
        row = s.get(RoutineItem, r.id) or RoutineItem(id=r.id)
        row.minutes, row.title, row.live_counter, row.sort = (
            r.minutes,
            r.title[:1].upper() + r.title[1:],
            r.live_counter,
            r.id,
        )
        row.detail = r.detail
        s.add(row)
        counts["routine"] += 1
    # Events, matched by name
    live_events = set()
    for e in spec.events:
        live_events.add(e.name)
        row = s.scalar(select(Event).where(Event.name == e.name)) or Event(name=e.name)
        row.city, row.country, row.start, row.end, row.segments, row.archived = (
            e.city,
            e.country,
            e.start,
            e.end,
            e.segments,
            False,
        )
        s.add(row)
        counts["events"] += 1
    for row in s.scalars(select(Event).where(Event.archived.is_(False))):
        if row.name not in live_events:
            row.archived = True
            counts["events_archived"] += 1
    set_meta(s, "campaign_start", spec.campaign_start.isoformat())
    set_meta(s, "outcome", spec.outcome)
    return counts


def sync_if_changed(s: Session, path: Path, force: bool = False) -> dict | None:
    """Re-sync when the file's modification time changed. Records parse errors instead of raising."""
    path = Path(path)
    if not path.is_file():
        set_meta(s, "plan_error", f"plan file not found: {path}")
        return None
    mtime = str(path.stat().st_mtime_ns)
    if not force and get_meta(s, "plan_mtime") == mtime and get_meta(s, "plan_error") is None:
        return None
    try:
        spec = parse_plan(path)
    except Exception as exc:  # keep serving the last good sync; show the error in the UI
        set_meta(s, "plan_error", f"{type(exc).__name__}: {exc}"[:500])
        set_meta(s, "plan_mtime", mtime)
        return None
    counts = sync_plan(s, spec)
    set_meta(s, "plan_mtime", mtime)
    set_meta(s, "plan_path", str(path))
    set_meta(s, "plan_synced_at", utcnow().isoformat(timespec="seconds") + "Z")
    set_meta(s, "plan_error", None)
    return counts
