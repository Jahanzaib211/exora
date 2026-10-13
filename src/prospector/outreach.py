"""Write path into the outreach engine (pipeline/outreach-engine).

Exora promotes companies to engine leads and drives the engine's own functions (enrol, run due steps,
approve drafts, log LinkedIn work), so every compliance gate in the engine still applies: region rules,
suppression, postal address, human approval of first emails, dry-run until switched off.

The engine app (LangGraph graph + checkpointer) is built once, lazily, and all calls are serialized: the
engine's SQLite checkpointer is not meant for concurrent writers.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import Company, CompanyStatus, Person, Signal, utcnow
from .normalize import domain_of

log = logging.getLogger(__name__)
EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT",
      "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}  # fmt: skip
SEGMENT_SEQUENCE = {
    "Law": "SEQ-LAW",
    "CPA": "SEQ-CPA",
    "Healthcare": "SEQ-HEALTH",
    "MSP": "SEQ-PARTNER",
    "AI startup": "SEQ-STARTUP",
    "KSA SME": "SEQ-KSA",
    "EU mid-market": "SEQ-EU",
}
SENIOR_ORDER = {"owner": 0, "c_level": 1, "partner": 2, "director": 3, "manager": 4, "staff": 5}


class OutreachError(RuntimeError):
    pass


_links: dict[str, OutreachLink] = {}
_links_lock = threading.Lock()


def get_link(settings) -> OutreachLink | None:
    """One engine link per process (shared by the API and the scheduler), or None if not configured."""
    if not getattr(settings, "engine_enabled", False) or not getattr(settings, "engine_config", None):
        return None
    key = str(settings.engine_config)
    with _links_lock:
        if key not in _links:
            _links[key] = OutreachLink.from_config(key)
        return _links[key]


def register_link(settings, link: OutreachLink) -> None:
    """Tests and embedders can inject a pre-built link."""
    with _links_lock:
        _links[str(settings.engine_config)] = link


def region_of(country: str | None) -> str:
    c = (country or "").upper()
    if c == "US":
        return "USA"
    if c == "GB":
        return "UK"
    if c == "SA":
        return "KSA"
    if c in EU:
        return "EU"
    return "Unconfirmed"  # the engine blocks outreach to unconfirmed regions until someone sets one


def sequence_for(segment: str | None, region: str) -> str:
    if region == "KSA":
        return "SEQ-KSA"  # LinkedIn-first, no cold email to Saudi personal addresses (PDPL)
    if region == "EU":
        return "SEQ-EU"  # LinkedIn-first; email only with a recorded lawful basis
    return SEGMENT_SEQUENCE.get(segment or "", "SEQ-PARTNER")


def priority_for(fit: int | None) -> str:
    return "A" if (fit or 0) >= 70 else "B" if (fit or 0) >= 50 else "C"


class OutreachLink:
    def __init__(self, engine_settings) -> None:
        self.settings = engine_settings
        self._app = None
        self._lock = threading.RLock()

    @classmethod
    def from_config(cls, config_path: str | None) -> OutreachLink:
        from outreach_engine.config import load_settings

        return cls(load_settings(config_path) if config_path else load_settings())

    def app(self):
        with self._lock:
            if self._app is None:
                from outreach_engine.runtime import build_app

                self._app = build_app(self.settings)
            return self._app

    def close(self) -> None:
        with self._lock:
            if self._app is not None:
                self._app.close()
                self._app = None

    # ---- promote --------------------------------------------------------------------------------------
    def promote(self, s: Session, company_ids: list[int], actor: str) -> list[dict[str, Any]]:
        from outreach_engine.models import Lead, LeadStatus

        results = []
        app = self.app()
        with self._lock, app.db.session() as es:
            for c in s.scalars(select(Company).where(Company.id.in_(company_ids))):
                region = region_of(c.country)
                people = s.scalars(select(Person).where(Person.company_id == c.id)).all()
                contact = min(
                    people, key=lambda p: (SENIOR_ORDER.get(p.seniority or "", 9), p.email is None), default=None
                )
                email = contact.email if contact and contact.email_status == "published" else c.general_email
                signal = s.scalar(
                    select(Signal.title)
                    .where(Signal.company_id == c.id)
                    .order_by((Signal.kind == "fit").desc(), Signal.created_at.desc())
                    .limit(1)
                )
                lead = es.get(Lead, c.engine_lead_id) if c.engine_lead_id else None
                if lead is None:
                    lead = self._match(es, c)
                created = lead is None
                if created:
                    lead = Lead(id=f"EX{c.id:05d}", status=LeadStatus.NEW)
                    es.add(lead)
                fill = {
                    "region": region,
                    "company": c.name,
                    "website": c.website,
                    "country": c.country,
                    "country_raw": c.country,
                    "city": c.city,
                    "segment": c.segment,
                    "sequence_id": sequence_for(c.segment, region),
                    "fit_signal": signal or "",
                    "source_url": c.website,
                    "named_contact": contact.name if contact else None,
                    "named_contact_title": contact.title if contact else None,
                    "email": email,
                    "email_source": "exora" if email else None,
                    "priority": priority_for(c.fit_score),
                    "linkedin_company_url": c.linkedin_url,
                    "company_phone": c.phone,
                    "retrieved_at_utc": utcnow().isoformat(timespec="seconds"),
                }
                for k, v in fill.items():
                    # a lead already in the engine keeps what the team set there; Exora only fills gaps
                    if created or getattr(lead, k, None) in (None, ""):
                        setattr(lead, k, v)
                c.engine_lead_id = lead.id
                c.status = CompanyStatus.IN_OUTREACH
                results.append(
                    {
                        "company_id": c.id,
                        "lead_id": lead.id,
                        "created": created,
                        "region": lead.region,
                        "sequence": lead.sequence_id,
                        "email": bool(lead.email),
                        "contact": lead.named_contact,
                    }
                )
        log.info("%s promoted %d companies to outreach", actor, len(results))
        return results

    @staticmethod
    def _match(es, c: Company):
        """An engine lead for the same company (e.g. from the original target sheet): same site or name."""
        from outreach_engine.models import Lead

        candidates = es.scalars(
            select(Lead).where(or_(func.lower(Lead.company) == c.name.lower(), Lead.website.is_not(None))).limit(5000)
        )
        for lead in candidates:
            if lead.company and lead.company.lower() == c.name.lower():
                return lead
            if c.domain and lead.website and domain_of(lead.website) == c.domain:
                return lead
        return None

    # ---- engine operations -------------------------------------------------------------------------------
    def enroll(self, lead_ids: list[str], actor: str) -> list[str]:
        from outreach_engine.runtime import enroll

        with self._lock:
            return enroll(self.app(), lead_ids, actor=actor)

    def tick(self) -> dict[str, Any]:
        """Run due sequence steps now (drafts, scheduled emails, LinkedIn tasks); poll replies if configured."""
        from outreach_engine.runtime import worker_tick

        with self._lock:
            app = self.app()
            summary = worker_tick(app)
            if any(os.environ.get(mb.password_env) for mb in app.settings.mailboxes):
                from outreach_engine.replies import poll_mailboxes

                summary["replies"] = poll_mailboxes(app)
        return {
            "steps": len(summary.get("steps", [])),
            "enrolled": len(summary.get("enrolled", [])),
            "flush": summary.get("flush", {}),
            "paused": summary.get("paused", False),
        }

    def summary(self) -> dict[str, Any]:
        from outreach_engine.models import Lead, Message, MessageStatus

        with self._lock:
            app = self.app()
            today = date.today()
            with app.db.session() as es:
                by_status = dict(es.execute(select(Lead.status, func.count()).group_by(Lead.status)).all())
                drafts = es.scalar(select(func.count()).where(Message.status == MessageStatus.PENDING_APPROVAL)) or 0
                li = (
                    es.scalar(
                        select(func.count()).where(
                            Message.channel == "linkedin",
                            Message.status == MessageStatus.TASK,
                            Message.task_date <= today,
                        )
                    )
                    or 0
                )
                sent = es.scalar(select(func.count()).where(Message.status == MessageStatus.SENT)) or 0
            return {
                "dry_run": app.settings.dry_run,
                "paused": app.settings.is_paused(),
                "leads_by_status": by_status,
                "pending_drafts": drafts,
                "linkedin_due": li,
                "emails_sent": sent,
                "postal_address_ok": app.settings.sender.postal_address_ok,
                "mailboxes": len(app.settings.mailboxes),
            }

    def drafts(self) -> list[dict[str, Any]]:
        from outreach_engine.approvals import list_pending

        with self._lock:
            return list_pending(self.app(), limit=200)

    def decide(self, message_id: int, action: str, reviewer: str, subject: str | None, body: str | None):
        from outreach_engine.approvals import DecisionError, decide

        with self._lock:
            try:
                return decide(self.app(), message_id, action, reviewer=reviewer, subject=subject, body=body).to_dict()
            except DecisionError as exc:
                raise OutreachError(str(exc)) from exc

    def linkedin(self, day: date | None = None) -> list[dict[str, Any]]:
        from outreach_engine.linkedin import tasks_for

        day = day or date.today()
        with self._lock:
            app = self.app()
            with app.db.session() as es:
                return [
                    {
                        "message_id": m.id,
                        "lead_id": lead.id,
                        "company": lead.company,
                        "region": lead.region,
                        "step": m.step_key,
                        "text": m.body,
                        "condition": m.condition,
                        "due": m.task_date.isoformat() if m.task_date else None,
                        "overdue": bool(m.task_date and m.task_date < day),
                        "person": lead.named_contact,
                        "title": lead.named_contact_title,
                        "target_roles": lead.target_roles,
                        "company_linkedin": lead.linkedin_company_url,
                        "warnings": m.warnings or [],
                    }
                    for m, lead in tasks_for(es, day)
                ]

    def log_linkedin(self, lead_id: str, event: str, step: str | None, actor: str) -> str:
        from outreach_engine.linkedin import log_event

        with self._lock:
            app = self.app()
            with app.db.session() as es:
                try:
                    return log_event(es, lead_id, event, step=step, actor=actor)
                except (ValueError, KeyError) as exc:
                    raise OutreachError(str(exc)) from exc

    def leads(self, q: str | None = None, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        from outreach_engine.models import Lead

        with self._lock:
            app = self.app()
            with app.db.session() as es:
                stmt = select(Lead)
                if q:
                    stmt = stmt.where(func.lower(Lead.company).contains(q.lower()))
                if status:
                    stmt = stmt.where(Lead.status == status)
                rows = es.scalars(
                    stmt.order_by(Lead.last_touch_at.desc().nullslast(), Lead.id.desc()).limit(limit)
                ).all()
                return [
                    {
                        "lead_id": lead.id,
                        "company": lead.company,
                        "region": lead.region,
                        "sequence": lead.sequence_id,
                        "status": lead.status,
                        "stop_reason": lead.stop_reason,
                        "step_index": lead.step_index,
                        "next_due_at": lead.next_due_at.isoformat() if lead.next_due_at else None,
                        "last_touch_at": lead.last_touch_at.isoformat() if lead.last_touch_at else None,
                        "email": lead.email,
                        "contact": lead.named_contact,
                    }
                    for lead in rows
                ]
