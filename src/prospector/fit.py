"""Fit score: how well a company matches ALI Technologies' ideal customer, with the reasons.

Rules come from 01_Market_Research_and_ICP.md and 04_Proven_Demand_and_Offers.md: target segments and
markets, a reachable decision-maker, signs the firm is established enough to pay, and signals that
private/sovereign AI is relevant (sensitive data in a public-cloud mailbox, AI interest on the site,
security/compliance posture, WhatsApp or booking channels for the voice and WhatsApp offers). Each rule is shown
to the user, so the score is never a black box.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Company, Person, Signal

ICP_SEGMENTS = {"Law", "CPA", "MSP", "Healthcare", "AI startup", "KSA SME", "EU mid-market"}
TARGET_MARKETS = {
    "US", "GB", "SA", "AE", "IE", "DE", "FR", "NL", "BE", "LU", "AT", "CH", "SE", "DK", "FI", "NO", "ES", "PT",
    "IT", "PL", "CZ",
}  # fmt: skip
SENIOR = ("owner", "c_level", "partner", "director")
ESTABLISHED_STAFF = 8  # named people on the firm's own site
ESTABLISHED_DOMAIN_YEARS = 5  # domain registered at least this long ago


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    points: int


RULES = [
    Rule("segment", "In a target segment", 15),
    Rule("market", "In a target market (US, UK, EU, Gulf)", 10),
    Rule(
        "established",
        f"Established firm ({ESTABLISHED_STAFF}+ named staff, active registration, known size, "
        f"or a domain {ESTABLISHED_DOMAIN_YEARS}+ years old)",
        10,
    ),
    Rule("decision_maker", "Named decision-maker found", 15),
    Rule("contact", "Business email or phone available", 10),
    Rule("cloud_mail", "Email hosted on Microsoft 365 or Google Workspace", 10),
    Rule("ai", "Website mentions AI", 10),
    Rule("compliance", "Security or data-protection posture on the website", 10),
    Rule("channel", "Uses WhatsApp or online booking (voice/WhatsApp offers)", 5),
    Rule("hiring", "Hiring (careers page or job board)", 5),
]
assert sum(r.points for r in RULES) == 100


def evaluate_fit(s: Session, c: Company) -> tuple[int, list[dict]]:
    seniorities = s.scalars(select(Person.seniority).where(Person.company_id == c.id)).all()
    staff = s.scalar(select(func.count(Person.id)).where(Person.company_id == c.id)) or 0
    mentions = set(
        s.scalars(select(Signal.title).where(Signal.company_id == c.id, Signal.kind == "site_mention")).all()
    )
    hiring = bool(c.careers_url or c.ats) or bool(
        s.scalar(select(Signal.id).where(Signal.company_id == c.id, Signal.kind == "hiring").limit(1))
    )
    met = {
        "segment": c.segment in ICP_SEGMENTS,
        "market": (c.country or "") in TARGET_MARKETS,
        "established": staff >= ESTABLISHED_STAFF
        or (c.registry_status or "").upper() == "ACTIVE"
        or bool(c.size_band)
        or bool(c.employees and c.employees >= 10)
        or bool(c.domain_created_on and (date.today() - c.domain_created_on).days >= ESTABLISHED_DOMAIN_YEARS * 365),
        "decision_maker": any(p in SENIOR for p in seniorities),
        "contact": bool(c.general_email or c.phone),
        "cloud_mail": c.email_provider in ("Microsoft 365", "Google Workspace"),
        "ai": "Website mentions AI adoption" in mentions,
        "compliance": bool(
            mentions
            & {
                "Website mentions Security certification",
                "Website mentions Data protection",
                "Website mentions Healthcare compliance",
            }
        ),
        "channel": bool(mentions & {"Website mentions WhatsApp channel", "Website mentions Online booking"}),
        "hiring": hiring,
    }
    reasons = [{"rule": r.key, "label": r.label, "points": r.points, "met": met[r.key]} for r in RULES]
    return sum(r.points for r in RULES if met[r.key]), reasons
