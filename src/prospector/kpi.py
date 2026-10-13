"""KPI and funnel evaluation: planned vs actual, with the 09 doc's target / kill-threshold logic."""

from __future__ import annotations

from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .engine_bridge import EngineSnapshot
from .models import Call, FunnelStage, KpiDef, KpiEntry


def rating(kpi: KpiDef, value: float | None, sample: int) -> str:
    """green = on target, red = threshold crossed (act now), amber = in between, gray = not enough data."""
    if value is None or sample < kpi.min_sample:
        return "gray"
    if kpi.direction == "higher":
        if value >= kpi.target:
            return "green"
        if kpi.threshold is not None and value < kpi.threshold:
            return "red"
        return "amber"
    if value <= kpi.target:
        return "green"
    if kpi.threshold is not None and value >= kpi.threshold:
        return "red"
    return "amber"


def _pct(a: int, b: int) -> float | None:
    return round(a / b * 100, 2) if b else None


def call_counts(s: Session) -> dict[str, int]:
    calls = s.scalars(select(Call)).all()
    held = [c for c in calls if c.held]
    return {
        "booked": len(calls),
        "held": len(held),
        "pilots": sum(1 for c in calls if c.outcome == "pilot"),
        "pilot_value": int(sum(c.value_usd or 0 for c in calls if c.outcome == "pilot")),
    }


def evaluate_kpis(s: Session, snap: EngineSnapshot) -> list[dict[str, Any]]:
    calls = call_counts(s)
    out = []
    for k in s.scalars(select(KpiDef).order_by(KpiDef.sort)):
        value: float | None = None
        sample = 0
        note = ""
        if k.source == "manual":
            entry = s.scalars(select(KpiEntry).where(KpiEntry.key == k.key).order_by(KpiEntry.on.desc())).first()
            if entry:
                value, sample, note = entry.value, 1, f"entered {entry.on.isoformat()}"
            else:
                note = "enter the latest reading"
        elif k.source == "calls":
            if k.key == "replies_to_calls":
                positive = snap.replies_positive + snap.li_replied if snap.available else 0
                value, sample = _pct(calls["booked"], positive), positive
            elif k.key == "calls_to_pilot":
                value, sample = _pct(calls["pilots"], calls["held"]), calls["held"]
        elif not snap.available:
            note = "outreach engine not connected"
        elif k.key == "li_acceptance":
            value, sample = _pct(snap.li_accepted, snap.li_requests), snap.li_requests
        elif k.key == "email_reply_rate":
            value, sample = _pct(snap.email_replies_human, snap.emails_sent), snap.emails_sent
        elif k.key == "hard_bounce":
            value = round(snap.bounce_rate_worst_today, 2) if snap.bounce_rate_worst_today is not None else None
            sample = snap.bounce_sample_today
        elif k.key == "reply_time":
            hours = snap.reply_hours or []
            value, sample = (round(median(hours), 1) if hours else None), len(hours)
            note = "median, clock hours"
        status = rating(k, value, sample)
        if status == "gray" and not note and sample < k.min_sample:
            note = f"collecting data ({sample}/{k.min_sample})"
        out.append(
            {
                "key": k.key,
                "name": k.name,
                "unit": k.unit,
                "direction": k.direction,
                "target": k.target,
                "threshold": k.threshold,
                "min_sample": k.min_sample,
                "action": k.action,
                "source": k.source,
                "value": value,
                "sample": sample,
                "status": status,
                "note": note,
            }
        )
    return out


def evaluate_funnel(s: Session, snap: EngineSnapshot) -> list[dict[str, Any]]:
    calls = call_counts(s)
    actual: dict[str, int | None] = {k: None for k in ("li_requests", "li_accepted", "li_replies", "emails_sent")}
    if snap.available:
        actual.update(
            li_requests=snap.li_requests,
            li_accepted=snap.li_accepted,
            li_replies=snap.li_replied,
            emails_sent=snap.emails_sent,
            email_replies=snap.email_replies_human,
            conversations=snap.li_replied + snap.email_replies_human,
        )
    actual["calls"] = calls["booked"]
    actual["pilots"] = calls["pilots"]
    rows = []
    for st in s.scalars(select(FunnelStage).order_by(FunnelStage.sort)):
        a = actual.get(st.key)
        progress = None if a is None or not st.plan_low else round(min(a / st.plan_low, 9.99) * 100, 1)
        rows.append(
            {
                "key": st.key,
                "name": st.name,
                "assumption": st.assumption,
                "source": st.source,
                "is_assumption": st.is_assumption,
                "plan_low": st.plan_low,
                "plan_high": st.plan_high,
                "actual": a,
                "progress_pct": progress,
            }
        )
    return rows
