"""Team tasks API and the promote-to-outreach flow against a real (temporary) outreach engine."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from prospector.api import create_app
from prospector.outreach import OutreachLink, register_link
from prospector.resolve import CompanyRecord, PersonRecord, upsert_company
from prospector.settings import load_settings

from .conftest import login, make_user


def seed(db, country="GB", segment="Law"):
    with db.session() as s:
        c, _ = upsert_company(
            s,
            CompanyRecord("osm", None, "Smith Law", website="smithlaw.co.uk", country=country, segment=segment,
                          general_email="info@smithlaw.co.uk",
                          people=[PersonRecord("Jane Doe", "Managing Partner", email="jane.doe@smithlaw.co.uk")]),
        )  # fmt: skip
        return c.id


def test_tasks_crud_and_permissions(app, db):
    cid = seed(db)
    sdr = login(TestClient(app), "sdr@test.io")
    admin = login(TestClient(app), "admin@test.io")
    users = {u["email"]: u["id"] for u in admin.get("/api/users").json()}
    due = (date.today() - timedelta(days=1)).isoformat()
    payload = {
        "title": "Research decision-makers",
        "kind": "research",
        "company_id": cid,
        "assignee_id": users["sdr@test.io"],
        "due_on": due,
        "priority": "high",
    }
    r = sdr.post("/api/tasks", json=payload)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["overdue"] and t["company"] == "Smith Law" and t["assignee"] == "Sdr"
    mine = sdr.get("/api/tasks", params={"assignee": "me", "due": "overdue"}).json()
    assert mine["total"] == 1
    summary = sdr.get("/api/tasks/summary").json()
    assert summary["mine"] == 1 and summary["mine_overdue"] == 1
    done = sdr.patch(f"/api/tasks/{t['id']}", json={"status": "done"}).json()
    assert done["status"] == "done" and done["completed_at"]
    assert sdr.get("/api/tasks", params={"assignee": "me"}).json()["total"] == 0  # done tasks leave the open list
    bulk = sdr.post("/api/tasks/bulk", json={"company_ids": [cid, 999], "title": "Call reception",
                                            "kind": "call"}).json()  # fmt: skip
    assert bulk == {"created": 1}
    viewer = login(TestClient(app), "viewer@test.io")
    assert viewer.post("/api/tasks", json={"title": "x"}).status_code == 403
    assert sdr.post("/api/tasks", json={"title": "x", "assignee_id": 999}).status_code == 400
    assert len(sdr.get(f"/api/companies/{cid}/tasks").json()) == 2


@pytest.fixture
def engine_app(tmp_path, db, settings):
    # The outreach engine is an optional, separately installed package: without it these tests skip.
    engine_config = pytest.importorskip("outreach_engine.config")
    EngineSettings = engine_config.Settings

    engine_settings = EngineSettings(
        _env_file=None,
        sender={"postal_address": "1 Test Street, Springfield 00000, Testland", "linkedin_handle": "test-sender"},
        mailboxes=[{"address": "sender@example.test", "smtp_host": "smtp.test", "password_env": "NOPE_PW",
                    "daily_cap": 25, "warmup_start": date(2026, 1, 1)}],
        dry_run=True,
        database_url=f"sqlite:///{(tmp_path / 'engine.db').as_posix()}",
        checkpoint_url=str(tmp_path / "ckpt.db"),
        data_dir=tmp_path / "engine-data",
        sending={"outbox_dir": tmp_path / "outbox"},
        llm_provider="fake",
        unsubscribe_secret="test-secret-long-enough-123",
    )  # fmt: skip
    s2 = load_settings(**{**settings.model_dump(), "engine_enabled": True, "engine_config": tmp_path / "engine.yaml"})
    link = OutreachLink(engine_settings)
    register_link(s2, link)
    for email, role in (("admin@test.io", "admin"), ("sdr@test.io", "sdr"), ("viewer@test.io", "viewer")):
        make_user(db, email, role)
    yield create_app(s2)
    link.close()


def test_promote_and_work_outreach(engine_app, db):
    cid = seed(db)
    c = login(TestClient(engine_app), "sdr@test.io")
    r = c.post("/api/outreach/promote", json={"ids": [cid], "enroll": True})
    assert r.status_code == 200, r.text
    body = r.json()
    p = body["promoted"][0]
    assert p["lead_id"] == f"EX{cid:05d}" and p["region"] == "UK" and p["sequence"] == "SEQ-LAW"
    assert p["email"] and p["contact"] == "Jane Doe"
    assert body["enrolled"] == [p["lead_id"]]
    company = c.get(f"/api/companies/{cid}").json()
    assert company["status"] == "in_outreach"
    summary = c.get("/api/outreach/summary").json()
    assert summary["dry_run"] is True and summary["leads_by_status"].get("active") == 1
    leads = c.get("/api/outreach/leads").json()
    assert leads[0]["company"] == "Smith Law" and leads[0]["sequence"] == "SEQ-LAW"
    assert isinstance(c.get("/api/outreach/drafts").json(), list)
    assert c.post("/api/outreach/drafts/999/decision", json={"action": "approve"}).status_code == 400
    # promoting again reuses the same engine lead (no duplicates)
    again = c.post("/api/outreach/promote", json={"ids": [cid], "enroll": False}).json()
    assert again["promoted"][0]["lead_id"] == p["lead_id"] and again["promoted"][0]["created"] is False


def test_ksa_companies_go_linkedin_first(engine_app, db):
    cid = seed(db, country="SA", segment="Law")
    c = login(TestClient(engine_app), "sdr@test.io")
    p = c.post("/api/outreach/promote", json={"ids": [cid], "enroll": False}).json()["promoted"][0]
    assert p["region"] == "KSA" and p["sequence"] == "SEQ-KSA"  # PDPL: LinkedIn-only sequence
