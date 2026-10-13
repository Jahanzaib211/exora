"""Read-only access to the outreach engine's database for live actuals.

Uses the engine's own settings and models, so there is one definition of "sent", "reply", "task".
Without the engine (or with engine_enabled=False) every actual is reported as unavailable, never as zero.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

log = logging.getLogger(__name__)

HUMAN_EXCLUDED = {"bounce", "out_of_office", "delivery_delay", "read_receipt"}


@dataclass
class EngineSnapshot:
    available: bool
    error: str | None = None
    dry_run: bool | None = None
    leads_by_status: dict[str, int] | None = None
    li_requests: int = 0
    li_accepted: int = 0
    li_replied: int = 0
    emails_sent: int = 0
    email_replies_human: int = 0
    replies_positive: int = 0
    bounce_rate_worst_today: float | None = None
    bounce_sample_today: int = 0
    reply_hours: list[float] | None = None
    pending_approvals: int = 0
    open_replies: int = 0
    linkedin_due: int = 0
    due_tomorrow: int = 0
    weekly: dict[str, dict[str, int]] | None = None  # per plan week label: li_requests, emails_sent
    mailboxes: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


class EngineBridge:
    def __init__(self, config_path: str | None = None) -> None:
        from outreach_engine.config import load_settings
        from outreach_engine.db import Database

        self.settings = load_settings(config_path) if config_path else load_settings()
        url = self.settings.database_url
        if url.startswith("sqlite:///"):
            # Read-only consumer: never create an empty engine database because a relative path resolved
            # against the wrong folder; report the misconfiguration instead.
            path = Path(url[len("sqlite:///") :])
            if not path.is_file():
                raise FileNotFoundError(f"outreach engine database not found at {path.resolve()}")
        self.db = Database(url)

    def snapshot(self, weeks: list[tuple[str, date, date]], today: date | None = None) -> EngineSnapshot:
        from outreach_engine.dispatch import sent_today
        from outreach_engine.models import Lead, LeadStatus, Message, MessageStatus, Reply, Touch
        from outreach_engine.scheduling import utcnow

        now = utcnow()
        today = today or now.date()
        snap = EngineSnapshot(available=True, dry_run=self.settings.dry_run)
        with self.db.session() as s:
            snap.leads_by_status = dict(s.execute(select(Lead.status, func.count()).group_by(Lead.status)).all())
            snap.li_requests = s.scalar(select(func.count()).where(Lead.li_connection_sent_at.is_not(None))) or 0
            snap.li_accepted = s.scalar(select(func.count()).where(Lead.li_accepted_at.is_not(None))) or 0
            snap.li_replied = s.scalar(select(func.count()).where(Lead.li_replied_at.is_not(None))) or 0
            snap.emails_sent = s.scalar(select(func.count()).where(Touch.event == "email_sent")) or 0
            classes = dict(s.execute(select(Reply.classification, func.count()).group_by(Reply.classification)).all())
            snap.email_replies_human = sum(v for k, v in classes.items() if k not in HUMAN_EXCLUDED)
            snap.replies_positive = classes.get("interested", 0) + classes.get("objection", 0)
            snap.pending_approvals = (
                s.scalar(select(func.count()).where(Message.status == MessageStatus.PENDING_APPROVAL)) or 0
            )
            snap.open_replies = (
                s.scalar(
                    select(func.count()).where(
                        Message.kind == "reply", Message.status == MessageStatus.PENDING_APPROVAL
                    )
                )
                or 0
            )
            snap.linkedin_due = (
                s.scalar(
                    select(func.count())
                    .select_from(Message)
                    .join(Lead, Lead.id == Message.lead_id)
                    .where(
                        Message.channel == "linkedin",
                        Message.status == MessageStatus.TASK,
                        Message.task_date <= today,
                        Lead.status != LeadStatus.STOPPED,
                    )
                )
                or 0
            )
            tomorrow_end = datetime.combine(today + timedelta(days=2), datetime.min.time())
            snap.due_tomorrow = (
                s.scalar(
                    select(func.count()).where(
                        Lead.status == LeadStatus.ACTIVE,
                        Lead.next_due_at >= datetime.combine(today + timedelta(days=1), datetime.min.time()),
                        Lead.next_due_at < tomorrow_end,
                    )
                )
                or 0
            )
            # Worst inbox bounce rate today
            start = datetime.combine(today, datetime.min.time())
            worst, sample = None, 0
            mailboxes = []
            for mb in self.settings.mailboxes:
                sent = sent_today(s, mb.address, now)
                bounces = (
                    s.scalar(
                        select(func.count()).where(
                            Touch.mailbox == mb.address, Touch.event == "bounce", Touch.at >= start
                        )
                    )
                    or 0
                )
                mailboxes.append({"address": mb.address, "sent_today": sent, "bounces_today": bounces})
                if sent:
                    rate = bounces / sent * 100
                    if worst is None or rate > worst:
                        worst, sample = rate, sent
            snap.bounce_rate_worst_today, snap.bounce_sample_today = worst, sample
            snap.mailboxes = mailboxes
            # Time from an inbound reply to our sent answer
            hours = []
            for received, sent_at in s.execute(
                select(Reply.received_at, Message.sent_at)
                .join(Message, Message.reply_to_id == Reply.id)
                .where(Message.kind == "reply", Message.sent_at.is_not(None))
            ):
                hours.append(max(0.0, (sent_at - received).total_seconds() / 3600))
            snap.reply_hours = hours
            weekly: dict[str, dict[str, int]] = {}
            for label, w_start, w_end in weeks:
                a = datetime.combine(w_start, datetime.min.time())
                b = datetime.combine(w_end + timedelta(days=1), datetime.min.time())
                weekly[label] = {
                    "li_requests": s.scalar(
                        select(func.count()).where(Lead.li_connection_sent_at >= a, Lead.li_connection_sent_at < b)
                    )
                    or 0,
                    "emails_sent": s.scalar(
                        select(func.count()).where(Touch.event == "email_sent", Touch.at >= a, Touch.at < b)
                    )
                    or 0,
                }
            snap.weekly = weekly
        return snap


def unavailable(reason: str) -> EngineSnapshot:
    return EngineSnapshot(available=False, error=reason)
