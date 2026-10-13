"""FastAPI app: JSON API under /api and the built front end at /.

Auth: HttpOnly session cookie (signed JWT), SameSite=Strict, plus a required X-Requested-With header on
every state-changing request (CSRF). Roles: admin > sdr > viewer.

Note: no ``from __future__ import annotations`` here. The request models are defined inside create_app, and
FastAPI must be able to resolve their annotations at definition time (string annotations of local classes
would be mistaken for query parameters).
"""

import logging
import math
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from . import outreach_api, prospects_api, tasks_api
from .branding import BRAND, TAGLINE, __version__
from .db import Database, audit
from .discovery import Worker
from .docs import catalog, read_doc
from .engine_bridge import EngineBridge, EngineSnapshot, unavailable
from .kpi import call_counts, evaluate_funnel, evaluate_kpis
from .models import (
    Audit,
    Call,
    Event,
    KpiDef,
    KpiEntry,
    PlanTask,
    PlanWeek,
    Role,
    RoutineCheck,
    RoutineItem,
    User,
    utcnow,
)
from .scheduler import Scheduler
from .security import (
    dummy_hash,
    hash_password,
    make_token,
    password_problems,
    read_token,
    verify_password,
)
from .seed import campaign_start, get_meta, sync_if_changed
from .settings import Settings

log = logging.getLogger(__name__)
COOKIE = "prospector_session"
CSRF_HEADER = "x-requested-with"
RANK = {Role.VIEWER: 0, Role.SDR: 1, Role.ADMIN: 2}
LOGIN_WINDOW_S, LOGIN_MAX_FAILS = 15 * 60, 5
# Computed once at import: a real scrypt hash with the same cost parameters as a stored one, so a
# login against an unknown email still spends the same ~0.1 s (see security.dummy_hash).
DUMMY_HASH = dummy_hash()


class AppState:
    def __init__(self, settings: Settings, db: Database, bridge: EngineBridge | None, bridge_error: str | None):
        self.settings = settings
        self.db = db
        self.bridge = bridge
        self.bridge_error = bridge_error
        self.login_fails: dict[str, list[float]] = defaultdict(list)
        # Same window, keyed on the client address: a spray across thousands of accounts never reaches
        # the per-account limit of 5, so the source needs a budget of its own.
        self.login_ip_fails: dict[str, list[float]] = defaultdict(list)
        # "METHOD status route-path" -> count, rendered by /api/metrics. Keyed on the route template
        # rather than the URL so ids cannot make the series cardinality explode.
        self.req_total: dict[str, int] = defaultdict(int)

    def today(self) -> date:
        return datetime.now(ZoneInfo(self.settings.operator_timezone)).date()

    def sync(self, s, force: bool = False) -> dict | None:
        """Pick up edits to the plan markdown (cheap mtime check on every plan-related request)."""
        return sync_if_changed(s, self.settings.plan_path, force=force)

    def plan_source(self, s) -> dict[str, Any]:
        return {
            "path": self.settings.plan_md,
            "synced_at": get_meta(s, "plan_synced_at"),
            "error": get_meta(s, "plan_error"),
            "outcome": get_meta(s, "outcome"),
        }

    def snapshot(self, s) -> EngineSnapshot:
        if self.bridge is None:
            return unavailable(self.bridge_error or "outreach engine disabled")
        weeks = [(w.label, w.start, w.end) for w in s.scalars(select(PlanWeek).order_by(PlanWeek.id))]
        try:
            return self.bridge.snapshot(weeks, self.today())
        except Exception as exc:  # the dashboard must still load if the engine DB is missing or locked
            log.exception("engine snapshot failed")
            return unavailable(f"{type(exc).__name__}: {exc}"[:200])


def create_app(settings: Settings, bridge: EngineBridge | None = None) -> FastAPI:
    db = Database(settings.database_url)
    db.create_all()
    with db.session() as s:
        sync_if_changed(s, settings.plan_path, force=True)
    bridge_error = None
    if bridge is None and settings.engine_enabled:
        try:
            bridge = EngineBridge(str(settings.engine_config) if settings.engine_config else None)
        except Exception as exc:
            bridge_error = f"engine not available: {type(exc).__name__}: {exc}"[:300]
            log.warning(bridge_error)
    state = AppState(settings, db, bridge, bridge_error)
    worker = Worker(db, settings) if settings.worker_enabled else None
    scheduler = (
        Scheduler(
            db,
            keep_awake=settings.keep_awake,
            outreach_enabled=bool(settings.engine_enabled and settings.engine_config),
        )
        if settings.scheduler_enabled
        else None
    )
    runtime: dict[str, Any] = {"worker": worker, "scheduler": scheduler}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if worker:
            worker.start()
        if scheduler:
            scheduler.start()
        yield
        if scheduler:
            scheduler.stop()
        if worker:
            worker.stop()

    app = FastAPI(
        title=BRAND,
        version=__version__,
        docs_url="/api/swagger",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.p = state
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    def client_ip(request: Request) -> str:
        """The caller's address as the outside world sees it.

        Behind Cloudflare Tunnel every connection terminates locally, so `request.client` is always
        127.0.0.1 and a per-IP throttle would be one bucket shared by the whole internet. Only trust
        the forwarding headers when the deployment says a proxy owns them, otherwise a client that
        reaches the app directly can pick any identity it likes.

        `CF-Connecting-IP` is preferred where present: Cloudflare overwrites it on the way in, so it
        cannot be forged through the tunnel the way a bare X-Forwarded-For can.
        """
        if settings.trust_proxy:
            cf_ip = request.headers.get("cf-connecting-ip", "").strip()
            if cf_ip:
                return cf_ip
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def is_https(request: Request) -> bool:
        if settings.trust_proxy:
            proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
            if proto:
                return proto == "https"
        return request.url.scheme == "https"

    def docs_allowed(request: Request) -> bool:
        """OpenAPI is an unauthenticated map of every route, parameter and model in the app.

        Kept off by default and checked off the event loop (it reads the database) so the guard can
        live in middleware, where it also covers FastAPI's own generated docs routes.
        """
        token = request.cookies.get(COOKIE)
        claims = read_token(token, settings.secret_key) if token else None
        if not claims:
            return False
        with db.session() as s:
            user = s.get(User, int(claims["sub"]))
        return bool(user and user.active and RANK[user.role] >= RANK[Role.ADMIN])

    # A reflected request id is a response-splitting vector; accept only a boring token.
    _REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        path = request.url.path
        rid = request.headers.get("x-request-id", "")
        if not _REQUEST_ID.fullmatch(rid):
            rid = uuid4().hex

        # Guards that must run before the handler sees the request.
        if settings.allowed_hosts:
            host = (request.headers.get("host") or "").split(":")[0].strip().lower()
            if host and host not in settings.allowed_hosts:
                return JSONResponse({"detail": "Unknown host"}, status_code=400, headers={"X-Request-ID": rid})
        docs_blocked = (
            not settings.api_docs_public
            and path in ("/api/swagger", "/api/openapi.json")
            and not await run_in_threadpool(docs_allowed, request)
        )
        if docs_blocked:
            # 404, not 401: an unauthenticated caller should not learn the schema exists.
            return JSONResponse({"detail": "Not Found"}, status_code=404, headers={"X-Request-ID": rid})

        response = await call_next(request)
        h = response.headers
        h["X-Request-ID"] = rid
        route = request.scope.get("route")
        state.req_total[f"{request.method} {response.status_code} {getattr(route, 'path', 'unmatched')}"] += 1
        if path.startswith("/assets/"):
            # file names carry a content hash: safe to cache for a year
            h["Cache-Control"] = "public, max-age=31536000, immutable"
        elif not path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-cache")  # index.html: always revalidate to pick up new builds
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if is_https(request):
            # Only ever sent on a request that actually arrived over HTTPS, so a plain-HTTP origin
            # cannot lock browsers out of itself.
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if not path.startswith("/api/swagger"):
            h.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            )
        if path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # Log everything; tell the user only that something failed (no stack traces or SQL in responses).
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Something went wrong. The error has been logged."})

    # ---- dependencies -------------------------------------------------------------------------------
    def st() -> AppState:
        return state

    def current_user(request: Request) -> User:
        token = request.cookies.get(COOKIE)
        claims = read_token(token, settings.secret_key) if token else None
        if not claims:
            raise HTTPException(401, "Please sign in")
        with db.session() as s:
            user = s.get(User, int(claims["sub"]))
            if user is None or not user.active:
                raise HTTPException(401, "This account is disabled")
            return user

    def require(role: str):
        def dep(request: Request, user: User = Depends(current_user)) -> User:
            if request.method not in ("GET", "HEAD") and request.headers.get(CSRF_HEADER) != "prospector":
                raise HTTPException(403, "missing X-Requested-With header")
            if RANK[user.role] < RANK[role]:
                raise HTTPException(403, "Your role does not allow this action")
            return user

        return dep

    viewer, sdr, admin = require(Role.VIEWER), require(Role.SDR), require(Role.ADMIN)
    prospects_api.register(app, db, viewer, sdr, admin, settings, runtime)
    outreach_api.register(app, db, viewer, sdr, settings)
    tasks_api.register(app, db, viewer, sdr)

    # ---- auth ---------------------------------------------------------------------------------------
    class LoginIn(BaseModel):
        email: str
        password: str

    def user_dict(u: User) -> dict[str, Any]:
        return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "active": u.active}

    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
        if request.headers.get(CSRF_HEADER) != "prospector":
            raise HTTPException(403, "missing X-Requested-With header")
        key = body.email.strip().lower()
        ip = client_ip(request)
        now = time.monotonic()

        def retry_after(times: list[float], limit: int) -> str:
            """Seconds until the throttle actually lifts: enough recorded failures must age out.

            Counting from the *oldest* would promise relief that never arrives once more than
            `limit` failures are in the window, so count from the one whose expiry drops the
            total below the limit.
            """
            ordered = sorted(times)
            if not ordered:
                return str(LOGIN_WINDOW_S)
            return str(max(1, math.ceil(ordered[max(len(ordered) - limit, 0)] + LOGIN_WINDOW_S - now)))

        # Prune every key, not just the one being tried. The dict was only trimmed for the address
        # being attempted, so an attacker spraying random emails grew it without bound.
        for stale_key in [
            k for k, times in state.login_fails.items() if not any(now - t < LOGIN_WINDOW_S for t in times)
        ]:
            del state.login_fails[stale_key]
        for stale_ip in [
            k for k, times in state.login_ip_fails.items() if not any(now - t < LOGIN_WINDOW_S for t in times)
        ]:
            del state.login_ip_fails[stale_ip]

        # Source address first: it is the cheaper signal, and answering it before the account lookup
        # keeps a spray from spending a scrypt hash per attempt.
        ip_fails = [t for t in state.login_ip_fails.get(ip, []) if now - t < LOGIN_WINDOW_S]
        state.login_ip_fails[ip] = ip_fails
        if len(ip_fails) >= settings.login_ip_max_fails:
            raise HTTPException(
                429,
                "too many failed attempts from this address; try again later",
                headers={"Retry-After": retry_after(ip_fails, settings.login_ip_max_fails)},
            )
        fails = [t for t in state.login_fails.get(key, []) if now - t < LOGIN_WINDOW_S]
        state.login_fails[key] = fails
        if len(fails) >= LOGIN_MAX_FAILS:
            raise HTTPException(
                429,
                "too many failed attempts; try again in 15 minutes",
                headers={"Retry-After": retry_after(fails, LOGIN_MAX_FAILS)},
            )
        with db.session() as s:
            user = s.scalar(select(User).where(User.email == key))
            # Always run the password check, even for an unknown email, so the response time does
            # not reveal which addresses exist (user enumeration via a timing oracle).
            password_ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
            valid = user is not None and user.active and password_ok
            user_id = user.id if user else None
            role = user.role if user else None
        if not valid:
            # Both counters mutate in place, and both lists are the very objects installed in
            # `state` above, so appending here is what fills them. Forgetting `ip_fails` left the
            # per-IP throttle checking a list that never grew: a spray across many addresses was
            # throttled only account-by-account, five at a time, which is exactly the case the
            # address limit exists for.
            fails.append(now)
            ip_fails.append(now)
            # Audited in its own session. `db.session()` rolls back on any exception, and the
            # HTTPException raised below is one, so auditing inside the same block discarded the
            # row every time — /api/audit had zero `login_failed` entries for the life of the database,
            # leaving the brute-force throttle with no forensic trail.
            with db.session() as s:
                audit(s, "login_failed", target=key)
            raise HTTPException(401, "wrong email or password")
        assert user_id is not None and role is not None  # noqa: S101 - guaranteed by `valid`
        with db.session() as s:
            user = s.get(User, user_id)
            if user is not None:
                user.last_login_at = utcnow()
            audit(s, "login", user_id)
            token = make_token(user_id, role, settings.secret_key, settings.session_hours)
            response.set_cookie(
                COOKIE,
                token,
                max_age=settings.session_hours * 3600,
                httponly=True,
                samesite=settings.cookie_same_site,
                secure=settings.cookie_secure,
                path="/",
            )
            return user_dict(user)

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        if request.headers.get(CSRF_HEADER) != "prospector":
            raise HTTPException(403, "missing X-Requested-With header")
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user: User = Depends(current_user)) -> dict[str, Any]:
        return user_dict(user)

    # ---- dashboard ----------------------------------------------------------------------------------
    def week_for(s, day: date) -> PlanWeek | None:
        weeks = s.scalars(select(PlanWeek).order_by(PlanWeek.id)).all()
        for w in weeks:
            if w.start <= day <= w.end + timedelta(days=2):  # weekend belongs to the week just finished
                return w
        upcoming = [w for w in weeks if w.start > day]
        return upcoming[0] if upcoming else (weeks[-1] if weeks else None)

    def live_tasks(s, week_id: int) -> list[PlanTask]:
        return s.scalars(select(PlanTask).where(PlanTask.week_id == week_id, PlanTask.archived.is_(False))).all()

    @app.get("/api/dashboard")
    def dashboard(user: User = Depends(viewer), p: AppState = Depends(st)) -> dict[str, Any]:
        today = p.today()
        with db.session() as s:
            p.sync(s)
            snap = p.snapshot(s)
            week = week_for(s, today)
            tasks = live_tasks(s, week.id) if week else []
            items = s.scalars(select(RoutineItem)).all()
            done = s.scalars(
                select(RoutineCheck).where(RoutineCheck.user_id == user.id, RoutineCheck.on == today)
            ).all()
            next_event = s.scalars(
                select(Event).where(Event.end >= today, Event.archived.is_(False)).order_by(Event.start)
            ).first()
            start = campaign_start(s) or today
            return {
                "today": today.isoformat(),
                "campaign": {
                    "start": start.isoformat(),
                    "day": (today - start).days + 1,
                    "starts_in_days": max(0, (start - today).days),
                },
                "plan_source": p.plan_source(s),
                "engine": {
                    "available": snap.available,
                    "error": snap.error,
                    "dry_run": snap.dry_run,
                    "leads_by_status": snap.leads_by_status,
                    "pending_approvals": snap.pending_approvals,
                    "open_replies": snap.open_replies,
                    "linkedin_due": snap.linkedin_due,
                },
                "week": _week_dict(week, tasks, snap) if week else None,
                "kpis": evaluate_kpis(s, snap),
                "funnel": evaluate_funnel(s, snap),
                "routine": {"done": len(done), "total": len(items), "minutes": sum(i.minutes for i in items)},
                "next_event": _event_dict(next_event, today) if next_event else None,
                "calls": call_counts(s),
                "warnings": _warnings(p, s),
            }

    def _warnings(p: AppState, s) -> list[str]:
        w = []
        err = get_meta(s, "plan_error")
        if err:
            w.append(f"Could not read {p.settings.plan_md}; showing the last good version. {err}")
        if p.settings.secret_is_default:
            w.append("PROSPECTOR_SECRET_KEY is the default or too short: set a random 32+ character value.")
        return w

    # ---- plan ---------------------------------------------------------------------------------------
    def _task_dict(t: PlanTask) -> dict[str, Any]:
        return {
            "id": t.id,
            "week_id": t.week_id,
            "track": t.track,
            "title": t.title,
            "detail": t.detail,
            "status": t.status,
            "assignee_id": t.assignee_id,
            "notes": t.notes,
        }

    def _week_dict(w: PlanWeek, tasks: list[PlanTask], snap: EngineSnapshot) -> dict[str, Any]:
        actual = (snap.weekly or {}).get(w.label) if snap.available else None
        return {
            "id": w.id,
            "label": w.label,
            "start": w.start.isoformat(),
            "end": w.end.isoformat(),
            "focus": w.focus,
            "targets": w.targets or {},
            "actual": actual,
            "tasks": [_task_dict(t) for t in sorted(tasks, key=lambda t: (t.track, t.sort))],
            "done": sum(1 for t in tasks if t.status == "done"),
            "total": len(tasks),
        }

    @app.get("/api/plan")
    def plan(user: User = Depends(viewer), p: AppState = Depends(st)) -> dict[str, Any]:
        today = p.today()
        with db.session() as s:
            p.sync(s)
            snap = p.snapshot(s)
            current = week_for(s, today)
            weeks = []
            for w in s.scalars(select(PlanWeek).order_by(PlanWeek.id)):
                d = _week_dict(w, live_tasks(s, w.id), snap)
                d["is_current"] = bool(current and current.id == w.id)
                weeks.append(d)
            return {
                "today": today.isoformat(),
                "weeks": weeks,
                "engine_available": snap.available,
                "plan_source": p.plan_source(s),
            }

    @app.post("/api/plan/resync")
    def resync(user: User = Depends(admin), p: AppState = Depends(st)) -> dict[str, Any]:
        with db.session() as s:
            counts = p.sync(s, force=True)
            audit(s, "plan_resynced", user.id, counts=counts)
            return {"counts": counts, "plan_source": p.plan_source(s)}

    class TaskPatch(BaseModel):
        status: str | None = Field(default=None, pattern="^(todo|doing|done|blocked)$")
        assignee_id: int | None = None
        notes: str | None = Field(default=None, max_length=4000)

    @app.patch("/api/plan/tasks/{task_id}")
    def patch_task(task_id: int, body: TaskPatch, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            t = s.get(PlanTask, task_id)
            if t is None:
                raise HTTPException(404, "no such task")
            changes = body.model_dump(exclude_unset=True)
            if changes.get("assignee_id") is not None:
                u = s.get(User, changes["assignee_id"])
                if u is None or not u.active:
                    raise HTTPException(400, "Choose an active team member")
            for k, v in changes.items():
                setattr(t, k, v)
            audit(s, "task_updated", user.id, target=f"task:{task_id}", **changes)
            s.flush()
            return _task_dict(t)

    # ---- KPIs and funnel ----------------------------------------------------------------------------
    @app.get("/api/kpis")
    def kpis(user: User = Depends(viewer), p: AppState = Depends(st)) -> dict[str, Any]:
        with db.session() as s:
            p.sync(s)
            snap = p.snapshot(s)
            history = [
                {"key": e.key, "value": e.value, "on": e.on.isoformat(), "note": e.note}
                for e in s.scalars(select(KpiEntry).order_by(KpiEntry.on.desc()).limit(100))
            ]
            return {
                "kpis": evaluate_kpis(s, snap),
                "funnel": evaluate_funnel(s, snap),
                "entries": history,
                "engine_available": snap.available,
                "engine_error": snap.error,
                "plan_source": p.plan_source(s),
            }

    class EntryIn(BaseModel):
        value: float = Field(ge=0, le=100000)
        on: date
        note: str | None = Field(default=None, max_length=1000)

    @app.post("/api/kpis/{key}/entries")
    def add_entry(key: str, body: EntryIn, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            k = s.get(KpiDef, key)
            if k is None:
                raise HTTPException(404, "KPI not found")
            if k.source != "manual":
                raise HTTPException(400, "this KPI is measured automatically")
            s.add(KpiEntry(key=key, value=body.value, on=body.on, note=body.note, user_id=user.id))
            audit(s, "kpi_entry", user.id, target=key, value=body.value)
            return {"ok": True}

    # ---- routine ------------------------------------------------------------------------------------
    @app.get("/api/routine")
    def routine(day: date | None = None, user: User = Depends(viewer), p: AppState = Depends(st)) -> dict[str, Any]:
        day = day or p.today()
        with db.session() as s:
            p.sync(s)
            snap = p.snapshot(s)
            counters = {
                "open_replies": snap.open_replies,
                "linkedin_due": snap.linkedin_due,
                "pending_approvals": snap.pending_approvals,
                "due_tomorrow": snap.due_tomorrow,
            }
            mine = {
                c.item_id
                for c in s.scalars(select(RoutineCheck).where(RoutineCheck.user_id == user.id, RoutineCheck.on == day))
            }
            team: dict[int, list[str]] = defaultdict(list)
            for c, u in s.execute(
                select(RoutineCheck, User).join(User, User.id == RoutineCheck.user_id).where(RoutineCheck.on == day)
            ):
                team[c.item_id].append(u.name)
            items = [
                {
                    "id": i.id,
                    "minutes": i.minutes,
                    "title": i.title,
                    "detail": i.detail,
                    "done": i.id in mine,
                    "team_done": team.get(i.id, []),
                    "live_count": counters.get(i.live_counter) if (i.live_counter and snap.available) else None,
                    "live_counter": i.live_counter,
                }
                for i in s.scalars(select(RoutineItem).order_by(RoutineItem.sort))
            ]
            return {"day": day.isoformat(), "items": items, "engine_available": snap.available}

    @app.post("/api/routine/{item_id}/toggle")
    def toggle_routine(item_id: int, user: User = Depends(sdr), p: AppState = Depends(st)) -> dict[str, Any]:
        day = p.today()
        with db.session() as s:
            if s.get(RoutineItem, item_id) is None:
                raise HTTPException(404, "no such routine item")
            existing = s.scalar(
                select(RoutineCheck).where(
                    RoutineCheck.item_id == item_id, RoutineCheck.user_id == user.id, RoutineCheck.on == day
                )
            )
            if existing:
                s.delete(existing)
                return {"done": False}
            s.add(RoutineCheck(item_id=item_id, user_id=user.id, on=day))
            return {"done": True}

    # ---- events -------------------------------------------------------------------------------------
    def _event_dict(e: Event, today: date) -> dict[str, Any]:
        return {
            "id": e.id,
            "name": e.name,
            "city": e.city,
            "country": e.country,
            "start": e.start.isoformat(),
            "end": e.end.isoformat(),
            "segments": e.segments or [],
            "status": e.status,
            "notes": e.notes,
            "days_until": (e.start - today).days,
            "is_past": e.end < today,
        }

    @app.get("/api/events")
    def events(user: User = Depends(viewer), p: AppState = Depends(st)) -> list[dict[str, Any]]:
        today = p.today()
        with db.session() as s:
            p.sync(s)
            return [
                _event_dict(e, today)
                for e in s.scalars(select(Event).where(Event.archived.is_(False)).order_by(Event.start))
            ]

    class EventPatch(BaseModel):
        status: str | None = Field(default=None, pattern="^(watch|prepare|outreach|attending|done)$")
        notes: str | None = Field(default=None, max_length=4000)

    @app.patch("/api/events/{event_id}")
    def patch_event(event_id: int, body: EventPatch, user: User = Depends(sdr), p: AppState = Depends(st)):
        with db.session() as s:
            e = s.get(Event, event_id)
            if e is None:
                raise HTTPException(404, "no such event")
            changes = body.model_dump(exclude_unset=True)
            for k, v in changes.items():
                setattr(e, k, v)
            audit(s, "event_updated", user.id, target=f"event:{event_id}", **changes)
            return _event_dict(e, p.today())

    # ---- calls --------------------------------------------------------------------------------------
    class CallIn(BaseModel):
        company: str = Field(min_length=1, max_length=300)
        lead_id: str | None = Field(default=None, max_length=32)
        contact: str | None = Field(default=None, max_length=200)
        source: str = Field(default="linkedin", pattern="^(linkedin|email|event|referral|other)$")
        scheduled_for: datetime | None = None
        held: bool = False
        outcome: str = Field(default="pending", pattern="^(pending|pilot|no|later)$")
        package: str | None = Field(default=None, pattern="^P(0[1-9]|1[0-2])$")
        value_usd: float | None = Field(default=None, ge=0, le=1_000_000)
        notes: str | None = Field(default=None, max_length=4000)

    class CallPatch(BaseModel):
        held: bool | None = None
        outcome: str | None = Field(default=None, pattern="^(pending|pilot|no|later)$")
        package: str | None = Field(default=None, pattern="^P(0[1-9]|1[0-2])$")
        value_usd: float | None = Field(default=None, ge=0, le=1_000_000)
        notes: str | None = Field(default=None, max_length=4000)
        scheduled_for: datetime | None = None

    def _call_dict(c: Call) -> dict[str, Any]:
        return {
            "id": c.id,
            "lead_id": c.lead_id,
            "company": c.company,
            "contact": c.contact,
            "source": c.source,
            "scheduled_for": c.scheduled_for.isoformat() if c.scheduled_for else None,
            "held": c.held,
            "outcome": c.outcome,
            "package": c.package,
            "value_usd": c.value_usd,
            "notes": c.notes,
            "owner_id": c.owner_id,
            "created_at": c.created_at.isoformat(),
        }

    @app.get("/api/calls")
    def list_calls(user: User = Depends(viewer)) -> list[dict[str, Any]]:
        with db.session() as s:
            return [_call_dict(c) for c in s.scalars(select(Call).order_by(Call.created_at.desc()))]

    @app.post("/api/calls")
    def add_call(body: CallIn, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            c = Call(**body.model_dump(), owner_id=user.id)
            s.add(c)
            s.flush()
            audit(s, "call_logged", user.id, target=f"call:{c.id}", company=c.company)
            return _call_dict(c)

    @app.patch("/api/calls/{call_id}")
    def patch_call(call_id: int, body: CallPatch, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            c = s.get(Call, call_id)
            if c is None:
                raise HTTPException(404, "no such call")
            changes = body.model_dump(exclude_unset=True)
            for k, v in changes.items():
                setattr(c, k, v)
            audit(s, "call_updated", user.id, target=f"call:{call_id}", **{k: str(v) for k, v in changes.items()})
            return _call_dict(c)

    # ---- users (admin) ------------------------------------------------------------------------------
    class UserIn(BaseModel):
        email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        name: str = Field(min_length=1, max_length=200)
        role: str = Field(pattern="^(admin|sdr|viewer)$")
        password: str = Field(min_length=1, max_length=200)

    class UserPatch(BaseModel):
        role: str | None = Field(default=None, pattern="^(admin|sdr|viewer)$")
        active: bool | None = None
        password: str | None = Field(default=None, max_length=200)

    @app.get("/api/users")
    def list_users(user: User = Depends(sdr)) -> list[dict[str, Any]]:
        with db.session() as s:
            return [user_dict(u) for u in s.scalars(select(User).order_by(User.name))]

    @app.post("/api/users")
    def add_user(body: UserIn, user: User = Depends(admin)) -> dict[str, Any]:
        problems = password_problems(body.password)
        if problems:
            raise HTTPException(400, "password: " + ", ".join(problems))
        with db.session() as s:
            email = body.email.strip().lower()
            if s.scalar(select(User).where(User.email == email)):
                raise HTTPException(409, "a user with that email exists")
            u = User(email=email, name=body.name, role=body.role, password_hash=hash_password(body.password))
            s.add(u)
            s.flush()
            audit(s, "user_created", user.id, target=email, role=body.role)
            return user_dict(u)

    @app.patch("/api/users/{user_id}")
    def patch_user(user_id: int, body: UserPatch, user: User = Depends(admin)) -> dict[str, Any]:
        with db.session() as s:
            u = s.get(User, user_id)
            if u is None:
                raise HTTPException(404, "no such user")
            if u.id == user.id and (body.active is False or (body.role and body.role != Role.ADMIN)):
                raise HTTPException(400, "you cannot demote or disable yourself")
            if body.password is not None:
                problems = password_problems(body.password)
                if problems:
                    raise HTTPException(400, "password: " + ", ".join(problems))
                u.password_hash = hash_password(body.password)
            if body.role is not None:
                u.role = body.role
            if body.active is not None:
                u.active = body.active
            audit(s, "user_updated", user.id, target=u.email, role=body.role, active=body.active)
            return user_dict(u)

    @app.get("/api/audit")
    def audit_log(user: User = Depends(admin)) -> list[dict[str, Any]]:
        with db.session() as s:
            return [
                {
                    "at": a.at.isoformat(),
                    "user_id": a.user_id,
                    "action": a.action,
                    "target": a.target,
                    "detail": a.detail,
                }
                for a in s.scalars(select(Audit).order_by(Audit.at.desc()).limit(300))
            ]

    # ---- docs (markdown suite, read only) -----------------------------------------------------------
    @app.get("/api/docs")
    def docs_list(user: User = Depends(viewer)) -> list[dict[str, Any]]:
        return catalog(settings.docs_root)

    @app.get("/api/docs/content")
    def docs_content(path: str, user: User = Depends(viewer)) -> dict[str, Any]:
        doc = read_doc(settings.docs_root, path)
        if doc is None:
            raise HTTPException(404, "not a published document")
        return doc

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "engine": state.bridge is not None}

    @app.get("/api/config")
    def public_config() -> dict[str, Any]:
        """What the front end needs before sign-in: display names, the operator clock and which optional
        features are switched on (so screens for an absent outreach engine never fire failing requests)."""
        return {
            "brand": BRAND,
            "tagline": TAGLINE,
            "org": settings.org_name,
            "timezone": settings.operator_timezone,
            "version": __version__,
            "outreach": bool(settings.engine_enabled and settings.engine_config),
        }

    # ---- observability ------------------------------------------------------------------------------
    # Registered only when asked for: an unauthenticated metrics endpoint is a request-rate oracle,
    # so the route is absent (404, not 403) unless PROSPECTOR_METRICS is on.
    if settings.metrics_enabled:
        metrics_started = time.time()

        @app.get("/api/metrics", include_in_schema=False)
        def metrics(request: Request) -> PlainTextResponse:
            # Scrapers cannot hold a session cookie, so a configured bearer token is accepted as an
            # alternative to an admin session. Nothing is exposed without one of the two.
            authorization = request.headers.get("authorization")
            token_ok = bool(settings.metrics_token and authorization == f"Bearer {settings.metrics_token}")
            if not token_ok and not docs_allowed(request):
                raise HTTPException(403, "metrics requires an admin session or a bearer token")
            lines = [
                "# HELP prospector_http_requests_total Completed HTTP requests by method, status and route.",
                "# TYPE prospector_http_requests_total counter",
            ]
            for key, value in sorted(state.req_total.items()):
                method, status, route = key.split(" ", 2)
                labels = ",".join(
                    f'{name}="{route.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
                    for name, route in (("method", method), ("status", status), ("path", route))
                )
                lines.append(f"prospector_http_requests_total{{{labels}}} {value}")
            lines += [
                "# TYPE prospector_process_start_time_seconds gauge",
                f"prospector_process_start_time_seconds {metrics_started:.0f}",
                "# TYPE prospector_uptime_seconds gauge",
                f"prospector_uptime_seconds {time.time() - metrics_started:.0f}",
                "# TYPE prospector_build_info gauge",
                f'prospector_build_info{{version="{app.version}"}} 1',
            ]
            return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    # ---- front end ----------------------------------------------------------------------------------
    dist = Path(settings.web_dist) if settings.web_dist else None
    if dist and (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app
