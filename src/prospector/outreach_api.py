"""Outreach endpoints: promote companies into the outreach engine and work its queues from Exora."""

from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import cache
from .db import Database, audit
from .models import Company, User
from .outreach import OutreachError, OutreachLink, get_link


class PromoteIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    enroll: bool = True


class DecisionIn(BaseModel):
    action: str = Field(pattern="^(approve|reject|skip)$")
    subject: str | None = Field(None, max_length=300)
    body: str | None = Field(None, max_length=10000)


class LinkedinIn(BaseModel):
    event: str = Field(pattern="^(sent|accepted|replied|skipped)$")
    step: str | None = Field(None, max_length=32)


def register(app: FastAPI, db: Database, viewer, sdr, settings) -> None:
    def link() -> OutreachLink:
        try:
            lk = get_link(settings)
        except Exception as exc:  # engine misconfigured: tell the user, keep the rest of the app working
            raise HTTPException(503, f"The outreach engine could not start: {exc}") from exc
        if lk is None:
            raise HTTPException(503, "The outreach engine is not connected. Set PROSPECTOR_ENGINE_CONFIG.")
        return lk

    def call(fn, *args):
        try:
            return fn(*args)
        except OutreachError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/outreach/summary")
    def summary(user: User = Depends(viewer)) -> dict[str, Any]:
        return link().summary()

    @app.post("/api/outreach/promote")
    def promote(body: PromoteIn, user: User = Depends(sdr)) -> dict[str, Any]:
        lk = link()
        with db.session() as s:
            missing = set(body.ids) - set(s.scalars(select(Company.id).where(Company.id.in_(body.ids))))
            if missing:
                raise HTTPException(404, f"{len(missing)} selected companies no longer exist")
            results = lk.promote(s, body.ids, user.email)
            audit(s, "promoted_to_outreach", user.id, count=len(results), enroll=body.enroll)
        enrolled: list[str] = []
        if body.enroll:
            enrolled = lk.enroll([r["lead_id"] for r in results], user.email)
        cache.bump()
        return {"promoted": results, "enrolled": enrolled}

    @app.post("/api/outreach/tick")
    def tick(user: User = Depends(sdr)) -> dict[str, Any]:
        result = link().tick()
        with db.session() as s:
            audit(s, "outreach_tick", user.id, **{k: v for k, v in result.items() if k != "flush"})
        return result

    @app.get("/api/outreach/drafts")
    def drafts(user: User = Depends(viewer)) -> list[dict[str, Any]]:
        return link().drafts()

    @app.post("/api/outreach/drafts/{message_id}/decision")
    def decide(message_id: int, body: DecisionIn, user: User = Depends(sdr)) -> dict[str, Any]:
        result = call(link().decide, message_id, body.action, user.email, body.subject, body.body)
        with db.session() as s:
            audit(s, f"draft_{body.action}", user.id, target=f"engine-message:{message_id}")
        return result

    @app.get("/api/outreach/linkedin")
    def linkedin(day: date | None = None, user: User = Depends(viewer)) -> list[dict[str, Any]]:
        return link().linkedin(day)

    @app.post("/api/outreach/linkedin/{lead_id}")
    def log_linkedin(lead_id: str, body: LinkedinIn, user: User = Depends(sdr)) -> dict[str, Any]:
        result = call(link().log_linkedin, lead_id, body.event, body.step, user.email)
        with db.session() as s:
            audit(s, f"linkedin_{body.event}", user.id, target=f"engine-lead:{lead_id}")
        return {"result": result}

    @app.get("/api/outreach/leads")
    def leads(
        q: str | None = Query(None, max_length=200),
        status: str | None = Query(None, pattern="^(new|active|awaiting_approval|completed|stopped)$"),
        user: User = Depends(viewer),
    ) -> list[dict[str, Any]]:
        return link().leads(q, status)
