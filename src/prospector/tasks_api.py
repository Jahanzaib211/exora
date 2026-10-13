"""Team tasks: assign research, calls, LinkedIn and follow-ups to teammates, with due dates."""

from datetime import date, timedelta
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select

from .db import Database, audit
from .models import Company, Person, Task, TaskStatus, User, utcnow

KINDS = "^(research|call|email|linkedin|meeting|follow_up|other)$"
PRIORITY = "^(low|normal|high)$"
STATUS = "^(open|in_progress|done|cancelled)$"


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    kind: str = Field("research", pattern=KINDS)
    company_id: int | None = None
    person_id: int | None = None
    assignee_id: int | None = None
    due_on: date | None = None
    priority: str = Field("normal", pattern=PRIORITY)
    notes: str | None = Field(None, max_length=5000)


class TaskPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    kind: str | None = Field(None, pattern=KINDS)
    assignee_id: int | None = None
    due_on: date | None = None
    priority: str | None = Field(None, pattern=PRIORITY)
    status: str | None = Field(None, pattern=STATUS)
    notes: str | None = Field(None, max_length=5000)


class BulkTaskIn(BaseModel):
    company_ids: list[int] = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=300)
    kind: str = Field("research", pattern=KINDS)
    assignee_id: int | None = None
    due_on: date | None = None
    priority: str = Field("normal", pattern=PRIORITY)


def task_row(t: Task, company: Company | None, person: Person | None, assignee: User | None) -> dict[str, Any]:
    today = date.today()
    return {
        "id": t.id,
        "title": t.title,
        "kind": t.kind,
        "status": t.status,
        "priority": t.priority,
        "due_on": t.due_on.isoformat() if t.due_on else None,
        "overdue": bool(t.due_on and t.due_on < today and t.status in TaskStatus.ACTIVE),
        "company_id": t.company_id,
        "company": company.name if company else None,
        "person_id": t.person_id,
        "person": person.name if person else None,
        "assignee_id": t.assignee_id,
        "assignee": assignee.name if assignee else None,
        "created_by": t.created_by,
        "notes": t.notes,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def register(app: FastAPI, db: Database, viewer, sdr) -> None:
    def _check_refs(s, company_id, person_id, assignee_id) -> None:
        if company_id is not None and not s.get(Company, company_id):
            raise HTTPException(404, "Company not found")
        if person_id is not None:
            p = s.get(Person, person_id)
            if p is None or (company_id is not None and p.company_id != company_id):
                raise HTTPException(400, "That person does not belong to this company")
        if assignee_id is not None:
            u = s.get(User, assignee_id)
            if u is None or not u.active:
                raise HTTPException(400, "Choose an active team member")

    def _rows(s, tasks: list[Task]) -> list[dict[str, Any]]:
        cids = {t.company_id for t in tasks if t.company_id}
        pids = {t.person_id for t in tasks if t.person_id}
        uids = {t.assignee_id for t in tasks if t.assignee_id}
        companies = {c.id: c for c in s.scalars(select(Company).where(Company.id.in_(cids)))} if cids else {}
        people = {p.id: p for p in s.scalars(select(Person).where(Person.id.in_(pids)))} if pids else {}
        users = {u.id: u for u in s.scalars(select(User).where(User.id.in_(uids)))} if uids else {}
        return [
            task_row(t, companies.get(t.company_id), people.get(t.person_id), users.get(t.assignee_id)) for t in tasks
        ]

    @app.get("/api/tasks")
    def list_tasks(
        assignee: str = Query("me", pattern=r"^(me|all|unassigned|\d+)$"),
        status: list[str] | None = Query(None),
        due: str | None = Query(None, pattern="^(overdue|today|week|later|none)$"),
        company_id: int | None = None,
        q: str | None = Query(None, max_length=200),
        page: int = Query(1, ge=1),
        page_size: int = Query(100, ge=1, le=500),
        user: User = Depends(viewer),
    ) -> dict[str, Any]:
        today = date.today()
        with db.session() as s:
            stmt = select(Task)
            if assignee == "me":
                stmt = stmt.where(Task.assignee_id == user.id)
            elif assignee == "unassigned":
                stmt = stmt.where(Task.assignee_id.is_(None))
            elif assignee.isdigit():
                stmt = stmt.where(Task.assignee_id == int(assignee))
            stmt = stmt.where(Task.status.in_(status or list(TaskStatus.ACTIVE)))
            if company_id:
                stmt = stmt.where(Task.company_id == company_id)
            if q:
                stmt = stmt.where(Task.title.ilike(f"%{q.replace('%', '').replace('_', '')}%"))
            if due == "overdue":
                stmt = stmt.where(Task.due_on < today)
            elif due == "today":
                stmt = stmt.where(Task.due_on == today)
            elif due == "week":
                stmt = stmt.where(and_(Task.due_on >= today, Task.due_on <= today + timedelta(days=7)))
            elif due == "later":
                stmt = stmt.where(Task.due_on > today + timedelta(days=7))
            elif due == "none":
                stmt = stmt.where(Task.due_on.is_(None))
            total = s.scalar(select(func.count()).select_from(stmt.subquery()))
            order = (
                Task.due_on.is_(None),
                Task.due_on,
                (Task.priority == "high").desc(),
                Task.created_at,
            )
            tasks = s.scalars(stmt.order_by(*order).offset((page - 1) * page_size).limit(page_size)).all()
            return {"items": _rows(s, tasks), "total": total}

    @app.get("/api/tasks/summary")
    def task_summary(user: User = Depends(viewer)) -> dict[str, Any]:
        today = date.today()
        with db.session() as s:
            active = Task.status.in_(TaskStatus.ACTIVE)

            def count(*conds) -> int:
                return s.scalar(select(func.count()).where(active, *conds)) or 0

            team = [
                {"user_id": uid, "name": name, "open": n}
                for uid, name, n in s.execute(
                    select(Task.assignee_id, User.name, func.count())
                    .join(User, User.id == Task.assignee_id)
                    .where(active)
                    .group_by(Task.assignee_id, User.name)
                    .order_by(func.count().desc())
                )
            ]
            return {
                "mine": count(Task.assignee_id == user.id),
                "mine_overdue": count(Task.assignee_id == user.id, Task.due_on < today),
                "mine_today": count(Task.assignee_id == user.id, Task.due_on == today),
                "unassigned": count(Task.assignee_id.is_(None)),
                "team": team,
            }

    @app.post("/api/tasks", status_code=201)
    def create_task(body: TaskIn, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            _check_refs(s, body.company_id, body.person_id, body.assignee_id)
            t = Task(**body.model_dump(), created_by=user.id)
            if t.assignee_id is None:
                t.assignee_id = user.id  # a task nobody owns is a task nobody does
            s.add(t)
            s.flush()
            audit(s, "task_created", user.id, target=f"task:{t.id}", assignee=t.assignee_id)
            return _rows(s, [t])[0]

    @app.post("/api/tasks/bulk", status_code=201)
    def bulk_tasks(body: BulkTaskIn, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            _check_refs(s, None, None, body.assignee_id)
            ids = list(s.scalars(select(Company.id).where(Company.id.in_(body.company_ids))))
            for cid in ids:
                s.add(
                    Task(
                        title=body.title, kind=body.kind, company_id=cid, assignee_id=body.assignee_id or user.id,
                        due_on=body.due_on, priority=body.priority, created_by=user.id,
                    )
                )  # fmt: skip
            audit(s, "tasks_bulk_created", user.id, count=len(ids), assignee=body.assignee_id)
            return {"created": len(ids)}

    def _editable(s, task_id: int, user: User) -> Task:
        t = s.get(Task, task_id)
        if t is None:
            raise HTTPException(404, "Task not found")
        if user.role != "admin" and user.id not in (t.created_by, t.assignee_id):
            raise HTTPException(403, "Only the assignee, the creator or an admin can change this task")
        return t

    @app.patch("/api/tasks/{task_id}")
    def patch_task(task_id: int, body: TaskPatch, user: User = Depends(sdr)) -> dict[str, Any]:
        changes = body.model_dump(exclude_unset=True)
        with db.session() as s:
            t = _editable(s, task_id, user)
            if "assignee_id" in changes:
                _check_refs(s, None, None, changes["assignee_id"])
            for k, v in changes.items():
                setattr(t, k, v)
            if "status" in changes:
                t.completed_at = utcnow() if t.status == TaskStatus.DONE else None
            audit(s, "task_updated", user.id, target=f"task:{task_id}", fields=list(changes))
            s.flush()
            return _rows(s, [t])[0]

    @app.delete("/api/tasks/{task_id}")
    def delete_task(task_id: int, user: User = Depends(sdr)) -> dict[str, int]:
        with db.session() as s:
            t = _editable(s, task_id, user)
            s.delete(t)
            audit(s, "task_deleted", user.id, target=f"task:{task_id}")
            return {"deleted": 1}

    @app.get("/api/companies/{company_id}/tasks")
    def company_tasks(company_id: int, user: User = Depends(viewer)) -> list[dict[str, Any]]:
        with db.session() as s:
            tasks = s.scalars(
                select(Task)
                .where(Task.company_id == company_id)
                .order_by(or_(Task.status == "done", Task.status == "cancelled"), Task.due_on.is_(None), Task.due_on)
            ).all()
            return _rows(s, tasks)


def overdue_count(db: Database, user_id: int) -> int:
    with db.session() as s:
        return (
            s.scalar(
                select(func.count()).where(
                    Task.assignee_id == user_id, Task.status.in_(TaskStatus.ACTIVE), Task.due_on < date.today()
                )
            )
            or 0
        )
