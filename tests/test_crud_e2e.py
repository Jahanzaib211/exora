"""End-to-end CRUD coverage for the API surface the front end depends on.

Every test runs against the isolated per-test SQLite database built by conftest (never the live
data/prospector.db) and checks four things per resource: the happy path, validation errors,
not-found behaviour, the real auth rule (401 without a session, 403 below the required role), and
that each write survives a re-read of the same data.
"""

import io
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from prospector import cache
from prospector.models import KpiDef, Run, Signal
from prospector.resolve import CompanyRecord, ExcludedRecord, PersonRecord, upsert_company

from .conftest import PASSWORD, login, make_user

# Every read the UI issues without a session must be refused with 401.
READS = [
    "/api/companies",
    "/api/companies/facets",
    "/api/companies/1",
    "/api/companies/1/notes",
    "/api/companies/1/tasks",
    "/api/companies/1/profile.docx",
    "/api/people",
    "/api/people/1",
    "/api/sources",
    "/api/runs",
    "/api/automations",
    "/api/signals",
    "/api/tasks",
    "/api/tasks/summary",
    "/api/dashboard",
    "/api/plan",
    "/api/kpis",
    "/api/routine",
    "/api/events",
    "/api/calls",
    "/api/quality",
    "/api/engine",
    "/api/docs",
    "/api/docs/content?path=x",
    "/api/exclusions",
    "/api/audit",
    "/api/users",
    "/api/export/companies?format=csv",
    "/api/export/people?format=csv",
    "/api/outreach/summary",
    "/api/outreach/drafts",
    "/api/outreach/leads",
]

# Every write the UI issues (path, JSON body or None): all need a session, all need at least the sdr role.
WRITES = [
    ("POST", "/api/companies", {"name": "Unauth Co"}),
    ("POST", "/api/companies/bulk", {"ids": [1], "action": "status", "status": "ready"}),
    ("POST", "/api/companies/1/notes", {"body": "hello"}),
    ("POST", "/api/companies/1/enrich", None),
    ("POST", "/api/people", {"company_id": 1, "name": "Ann Lee"}),
    ("POST", "/api/runs", {"source": "osm", "params": {}}),
    ("POST", "/api/runs/1/cancel", None),
    ("POST", "/api/runs/1/retry", None),
    ("POST", "/api/automations", {"name": "a", "source": "osm"}),
    ("POST", "/api/automations/1/run", None),
    ("POST", "/api/tasks", {"title": "t"}),
    ("POST", "/api/tasks/bulk", {"company_ids": [1], "title": "t"}),
    ("POST", "/api/kpis/spam_rate/entries", {"value": 1, "on": "2026-10-01"}),
    ("POST", "/api/routine/1/toggle", None),
    ("POST", "/api/calls", {"company": "Acme"}),
    ("POST", "/api/users", {"email": "x@y.io", "name": "X", "role": "sdr", "password": "Abcdefghij12"}),
    ("POST", "/api/plan/resync", None),
    ("POST", "/api/outreach/promote", {"ids": [1]}),
    ("POST", "/api/outreach/tick", None),
    ("POST", "/api/outreach/drafts/1/decision", {"action": "approve"}),
    ("POST", "/api/outreach/linkedin/L1", {"event": "sent"}),
    ("PATCH", "/api/companies/1", {"city": "X"}),
    ("PATCH", "/api/notes/1", {"body": "x"}),
    ("PATCH", "/api/people/1", {"title": "CTO"}),
    ("PATCH", "/api/automations/1", {"enabled": True}),
    ("PATCH", "/api/tasks/1", {"status": "done"}),
    ("PATCH", "/api/plan/tasks/1", {"status": "done"}),
    ("PATCH", "/api/events/1", {"status": "done"}),
    ("PATCH", "/api/calls/1", {"held": True}),
    ("PATCH", "/api/users/1", {"role": "viewer"}),
    ("DELETE", "/api/companies/1", None),
    ("DELETE", "/api/notes/1", None),
    ("DELETE", "/api/people/1", None),
    ("DELETE", "/api/automations/1", None),
    ("DELETE", "/api/tasks/1", None),
    ("DELETE", "/api/exclusions/1", None),
]

# Reads that work for every role (viewer included).
READER_READS = [
    "/api/companies",
    "/api/companies/facets",
    "/api/companies/1",
    "/api/companies/1/notes",
    "/api/companies/1/tasks",
    "/api/people",
    "/api/people/1",
    "/api/sources",
    "/api/runs",
    "/api/automations",
    "/api/signals",
    "/api/tasks",
    "/api/tasks/summary",
    "/api/dashboard",
    "/api/plan",
    "/api/kpis",
    "/api/routine",
    "/api/events",
    "/api/calls",
    "/api/quality",
    "/api/engine",
    "/api/docs",
]


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Facets/quality are cached process-wide by data version; reset so a test can never be served an
    aggregate computed against a previous test's (different) database."""
    cache.bump()


def client(app, role: str = "sdr") -> TestClient:
    """A logged-in client for one of the seeded accounts."""
    return login(TestClient(app), f"{role}@test.io")


def request(c: TestClient, method: str, path: str, body):
    if method == "POST":
        return c.post(path) if body is None else c.post(path, json=body)
    if method == "PATCH":
        return c.patch(path, json=body)
    return c.delete(path)


def seed(db, n=3, segment="Law"):
    """n companies from the osm source, one named contact each, alternating segment."""
    ids = []
    with db.session() as s:
        for i in range(n):
            c, _ = upsert_company(
                s,
                CompanyRecord(
                    "osm",
                    None,
                    f"Firm {i}",
                    website=f"firm{i}.com",
                    country="GB",
                    segment=segment if i % 2 == 0 else "CPA",
                    people=[PersonRecord(f"Contact{i} Doe", "Managing Partner", email=f"contact{i}@firm{i}.com")],
                ),
            )
            ids.append(c.id)
    return ids


# ---- auth and roles -------------------------------------------------------------------------------
def test_auth_login_me_logout(app):
    c = TestClient(app)
    assert c.get("/api/auth/me").status_code == 401
    # login itself needs the CSRF header
    assert c.post("/api/auth/login", json={"email": "admin@test.io", "password": PASSWORD}).status_code == 403
    bad = c.post(
        "/api/auth/login",
        json={"email": "admin@test.io", "password": "Nope-nope-1234"},
        headers={"X-Requested-With": "prospector"},
    )
    assert bad.status_code == 401
    ok = c.post(
        "/api/auth/login",
        json={"email": " ADMIN@Test.io ", "password": PASSWORD},
        headers={"X-Requested-With": "prospector"},
    )
    assert ok.status_code == 200 and ok.json()["role"] == "admin" and ok.json()["active"] is True
    me = c.get("/api/auth/me").json()
    assert me["email"] == "admin@test.io" and me["id"] > 0 and me["name"] == "Admin"
    # a valid session without the CSRF header still cannot write
    c.headers["X-Requested-With"] = ""
    assert c.post("/api/calls", json={"company": "Acme"}).status_code == 403
    assert c.post("/api/auth/logout").status_code == 403  # logout changes state too
    c.headers["X-Requested-With"] = "prospector"
    assert c.post("/api/auth/logout").json() == {"ok": True}
    assert c.get("/api/auth/me").status_code == 401
    assert c.post("/api/calls", json={"company": "Acme"}).status_code == 401


def test_every_endpoint_requires_a_session(app):
    c = TestClient(app)
    for path in READS:
        assert c.get(path).status_code == 401, path
    for method, path, body in WRITES:
        assert request(c, method, path, body).status_code == 401, f"{method} {path}"
    # the health check is deliberately public
    assert c.get("/api/health").json() == {"ok": True, "engine": False}
    # so is the display config the sign-in page needs, and it carries nothing but names and the clock
    cfg = c.get("/api/config").json()
    assert set(cfg) == {"brand", "tagline", "org", "timezone", "version"}
    assert cfg["brand"] == "Exora" and cfg["timezone"] == "UTC"


def test_viewer_can_read_everything(app, db):
    seed(db, 1)
    v = client(app, "viewer")
    for path in READER_READS:
        assert v.get(path).status_code == 200, path
    # reads that are gated above the viewer role
    for path in ("/api/exclusions", "/api/audit", "/api/users", "/api/export/companies?format=csv"):
        assert v.get(path).status_code == 403, path


def test_viewer_cannot_write_anything(app, db):
    v = client(app, "viewer")
    for method, path, body in WRITES:
        assert request(v, method, path, body).status_code == 403, f"{method} {path}"


def test_sdr_cannot_perform_admin_writes(app, db):
    sdr = client(app, "sdr")
    admin = client(app, "admin")
    # sdr-level reads work
    assert sdr.get("/api/exclusions").status_code == 200
    assert sdr.get("/api/users").status_code == 200
    # admin-only endpoints refuse the sdr
    assert sdr.get("/api/audit").status_code == 403
    assert (
        sdr.post(
            "/api/users", json={"email": "a@b.co", "name": "A", "role": "sdr", "password": "Abcdefghij12"}
        ).status_code
        == 403
    )
    assert sdr.patch("/api/users/1", json={"role": "viewer"}).status_code == 403
    assert sdr.post("/api/plan/resync").status_code == 403
    assert sdr.delete("/api/exclusions/1").status_code == 403
    # the same requests pass for an admin (audit list is readable before any company write)
    assert admin.get("/api/audit").status_code == 200
    assert admin.post("/api/plan/resync").status_code == 200


# ---- companies ------------------------------------------------------------------------------------
def test_company_list_filters_facets_and_validation(app, db):
    ids = seed(db, 5)
    c = client(app, "sdr")
    made = c.post("/api/companies", json={"name": "Quiet Trading", "country": "GB", "segment": "Law"})
    assert made.status_code == 201, made.text
    page = c.get("/api/companies", params={"page_size": 2, "sort": "name"}).json()
    assert page["total"] == 6 and len(page["items"]) == 2
    last = c.get("/api/companies", params={"page_size": 2, "page": 3, "sort": "name"}).json()
    assert last["total"] == 6 and len(last["items"]) == 2
    # search (case-insensitive, LIKE wildcards escaped)
    assert c.get("/api/companies", params={"q": "firm 3"}).json()["total"] == 1
    assert c.get("/api/companies", params={"q": "QUIET"}).json()["total"] == 1
    assert c.get("/api/companies", params={"q": "%"}).json()["total"] == 0
    # facet-style filters
    assert c.get("/api/companies", params={"segment": ["Law"]}).json()["total"] == 4
    assert c.get("/api/companies", params={"country": ["gb"]}).json()["total"] == 6
    assert c.get("/api/companies", params={"status": ["rejected"]}).json()["total"] == 0
    assert c.get("/api/companies", params={"has_email": False}).json()["total"] == 1
    assert c.get("/api/companies", params={"has_people": True}).json()["total"] == 5
    assert c.get("/api/companies", params={"owner_id": [1]}).json()["total"] == 0
    # sort and paging validation
    assert c.get("/api/companies", params={"page": 0}).status_code == 422
    assert c.get("/api/companies", params={"min_quality": 101}).status_code == 422
    assert c.get("/api/companies", params={"sort": "bogus"}).status_code == 422
    # the facets the dashboard and the list sidebar show
    facets = c.get("/api/companies/facets").json()
    assert facets["total"] == 6 and facets["people"] == 5
    assert {"value": "Law", "count": 4} in facets["segment"]
    assert {"value": "new", "count": 6} in facets["status"]
    assert {"value": "GB", "count": 6} in facets["country"]
    assert any(s["value"] == "osm" for s in facets["source"])
    assert ids  # the seeded companies are the ones counted above


def test_company_create_detail_update_and_404s(app, db):
    c = client(app, "sdr")
    created = c.post(
        "/api/companies",
        json={
            "name": "Acme Advisory",
            "website": "https://acme-advisory.com",
            "country": "United Kingdom",
            "phone": "0161 832 1234",
            "segment": "CPA",
            "general_email": "hello@acme-advisory.com",
        },
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    assert created.json()["country"] == "GB"
    assert created.json()["phone"] == "+441618321234"
    assert created.json()["domain"] == "acme-advisory.com"
    detail = c.get(f"/api/companies/{cid}").json()
    assert detail["segment"] == "CPA" and detail["general_email"] == "hello@acme-advisory.com"
    assert detail["quality_score"] is not None
    assert detail["tier"] in ("golden", "verified", "partial", "raw")
    assert isinstance(detail["fit_reasons"], list)
    assert detail["sources"] and all(o["source"] == "manual" for o in detail["sources"])
    # validation errors
    assert c.post("/api/companies", json={"name": ""}).status_code == 422
    assert c.post("/api/companies", json={"name": "No Website Co", "website": "https://"}).status_code == 400
    assert c.post("/api/companies", json={"name": "Bad Country", "country": "Atlantis"}).status_code == 400
    assert c.post("/api/companies", json={"name": "Bad Phone", "phone": "12"}).status_code == 400
    assert c.post("/api/companies", json={"name": "Bad Email", "general_email": "nope"}).status_code == 400
    dup = c.post("/api/companies", json={"name": "Acme Advisory Ltd", "website": "www.acme-advisory.com"})
    assert dup.status_code == 409 and dup.json()["detail"]["existing_id"] == cid
    # nothing was written by the rejected attempts
    assert c.get("/api/companies").json()["total"] == 1
    assert c.patch(f"/api/companies/{cid}", json={"status": "bogus"}).status_code == 422
    assert c.patch(f"/api/companies/{cid}", json={"phone": "call me"}).status_code == 400
    assert c.patch(f"/api/companies/{cid}", json={"general_email": "not-an-email"}).status_code == 400
    assert c.patch(f"/api/companies/{cid}", json={"website": "https://"}).status_code == 400
    # a website that already belongs to another company is refused
    other = c.post("/api/companies", json={"name": "Acme Two"})
    assert other.status_code == 201
    conflict = c.patch(f"/api/companies/{other.json()['id']}", json={"website": "acme-advisory.com"})
    assert conflict.status_code == 409 and conflict.json()["detail"]["existing_id"] == cid
    # update persists, with a provenance trail
    upd = c.patch(f"/api/companies/{cid}", json={"city": "Manchester", "status": "ready", "size_band": "11-50"})
    assert upd.status_code == 200 and upd.json()["city"] == "Manchester"
    reread = c.get(f"/api/companies/{cid}").json()
    assert reread["city"] == "Manchester" and reread["status"] == "ready" and reread["size_band"] == "11-50"
    assert any(o["field"] == "city" and o["source"] == "manual" for o in reread["sources"])
    # 404s
    assert c.get("/api/companies/999999").status_code == 404
    assert c.patch("/api/companies/999999", json={"city": "X"}).status_code == 404
    assert c.delete("/api/companies/999999").status_code == 404


def test_company_owner_is_validated_and_persisted(app, db):
    c = client(app, "sdr")
    users = client(app, "admin").get("/api/users").json()
    admin_id = next(u["id"] for u in users if u["email"] == "admin@test.io")
    # an owner that is not a team member is refused and the row is not created
    bad = c.post("/api/companies", json={"name": "Owned Co", "owner_id": 999999})
    assert bad.status_code == 400, bad.text
    assert c.get("/api/companies").json()["total"] == 0
    cid = seed(db, 1)[0]
    assert c.patch(f"/api/companies/{cid}", json={"owner_id": 999999}).status_code == 400
    assert c.post("/api/companies/bulk", json={"ids": [cid], "action": "owner", "owner_id": 999999}).status_code == 400
    ok = c.patch(f"/api/companies/{cid}", json={"owner_id": admin_id})
    assert ok.status_code == 200 and ok.json()["owner_id"] == admin_id
    assert c.get(f"/api/companies/{cid}").json()["owner"] == {"id": admin_id, "name": "Admin"}
    # unassigning (null) is allowed
    assert c.patch(f"/api/companies/{cid}", json={"owner_id": None}).json()["owner_id"] is None


def test_company_delete_and_bulk(app, db):
    ids = seed(db, 2)
    c = client(app, "sdr")
    assert c.delete(f"/api/companies/{ids[0]}").json() == {"deleted": 1}
    assert c.get(f"/api/companies/{ids[0]}").status_code == 404
    assert c.get("/api/companies").json()["total"] == 1
    assert c.delete(f"/api/companies/{ids[0]}").status_code == 404
    # the deleted company's people go with it
    people = c.get("/api/people").json()
    assert people["total"] == 1 and all(p["company_id"] != ids[0] for p in people["items"])
    # bulk actions
    assert c.post("/api/companies/bulk", json={"ids": [ids[1]], "action": "status", "status": "ready"}).json() == {
        "updated": 1
    }
    assert c.get(f"/api/companies/{ids[1]}").json()["status"] == "ready"
    assert c.post("/api/companies/bulk", json={"ids": [ids[1]], "action": "delete"}).json() == {"deleted": 1}
    assert c.get("/api/companies").json()["total"] == 0
    assert c.post("/api/companies/bulk", json={"ids": [], "action": "delete"}).status_code == 422
    assert c.post("/api/companies/bulk", json={"ids": [1], "action": "explode"}).status_code == 422
    assert c.post("/api/companies/bulk", json={"ids": [1], "action": "status"}).status_code == 400


def test_company_enrich_validates_the_company(app, db):
    c = client(app, "sdr")
    viewer = client(app, "viewer")
    assert c.post("/api/companies/999999/enrich").status_code == 404
    made = c.post("/api/companies", json={"name": "No Website Trading"})
    assert made.status_code == 201
    no_site = c.post(f"/api/companies/{made.json()['id']}/enrich")
    assert no_site.status_code == 400  # exists, but there is nothing to crawl
    assert viewer.post("/api/companies/1/enrich").status_code == 403
    assert c.get("/api/runs").json() == []  # neither refused attempt queued work


def test_company_and_people_exports(app, db):
    ids = seed(db, 2)
    c = client(app, "sdr")
    csv_r = c.get("/api/export/companies", params={"format": "csv"})
    assert csv_r.status_code == 200 and csv_r.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_r.headers["content-disposition"]
    assert "Firm 0" in csv_r.content.decode("utf-8-sig")
    x = c.get("/api/export/companies", params={"format": "xlsx", "ids": [ids[0]]})
    assert x.status_code == 200 and x.content[:2] == b"PK"
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Companies", "People", "About this export"]
    assert wb["Companies"]["B2"].value.startswith("Firm")
    assert c.get("/api/export/companies", params={"format": "pdf"}).status_code == 422
    people = c.get("/api/export/people", params={"format": "csv", "has_email": True})
    assert people.status_code == 200 and "Contact0 Doe" in people.content.decode("utf-8-sig")
    assert c.get("/api/export/people", params={"format": "xlsx", "q": "Ann Lee"}).status_code == 200
    profile = c.get(f"/api/companies/{ids[0]}/profile.docx")
    assert profile.status_code == 200 and profile.content[:2] == b"PK"
    assert c.get("/api/companies/999999/profile.docx").status_code == 404
    viewer = client(app, "viewer")
    assert viewer.get("/api/export/companies").status_code == 403
    assert viewer.get("/api/export/people").status_code == 403
    assert viewer.get(f"/api/companies/{ids[0]}/profile.docx").status_code == 403


# ---- notes ----------------------------------------------------------------------------------------
def test_notes_crud_permissions_and_persistence(app, db):
    cid = seed(db, 1)[0]
    make_user(db, "sdr2@test.io", "sdr")
    c = client(app, "sdr")
    sdr2 = client(app, "sdr2")
    viewer = client(app, "viewer")
    assert c.post(f"/api/companies/{cid}/notes", json={"body": ""}).status_code == 422
    assert c.post(f"/api/companies/{cid}/notes", json={"body": "   "}).status_code == 400  # empty after trimming
    assert c.post("/api/companies/999999/notes", json={"body": "x"}).status_code == 404
    assert viewer.post(f"/api/companies/{cid}/notes", json={"body": "x"}).status_code == 403
    n = c.post(f"/api/companies/{cid}/notes", json={"body": "  Left a voicemail.  "})
    assert n.status_code == 201 and n.json()["body"] == "Left a voicemail."
    nid = n.json()["id"]
    rows = c.get(f"/api/companies/{cid}/notes").json()
    assert len(rows) == 1 and rows[0]["user"] == "Sdr" and rows[0]["user_id"] == n.json()["user_id"]
    assert c.get(f"/api/companies/{cid}").json()["notes_count"] == 1
    # only the author or an admin may change a note
    assert sdr2.patch(f"/api/notes/{nid}", json={"body": "nope"}).status_code == 403
    assert sdr2.delete(f"/api/notes/{nid}").status_code == 403
    assert viewer.patch(f"/api/notes/{nid}", json={"body": "nope"}).status_code == 403
    edit = c.patch(f"/api/notes/{nid}", json={"body": "Called back, spoke to the partner."})
    assert edit.status_code == 200 and edit.json()["body"] == "Called back, spoke to the partner."
    assert c.get(f"/api/companies/{cid}/notes").json()[0]["body"] == "Called back, spoke to the partner."
    assert c.patch(f"/api/notes/{nid}", json={"body": " "}).status_code == 400
    assert c.patch("/api/notes/999999", json={"body": "x"}).status_code == 404
    assert c.delete("/api/notes/999999").status_code == 404
    # an admin may edit someone else's note (documented rule)
    assert client(app, "admin").patch(f"/api/notes/{nid}", json={"body": "Edited by admin"}).status_code == 200
    # delete persists
    assert c.delete(f"/api/notes/{nid}").json() == {"deleted": 1}
    assert c.get(f"/api/companies/{cid}/notes").json() == []
    assert c.delete(f"/api/notes/{nid}").status_code == 404
    # a company that does not exist has no notes to list (empty collection, not an error)
    assert c.get("/api/companies/999999/notes").json() == []


# ---- people ---------------------------------------------------------------------------------------
def test_people_crud_filters_and_persistence(app, db):
    cid = seed(db, 1)[0]
    c = client(app, "sdr")
    viewer = client(app, "viewer")
    assert viewer.post("/api/people", json={"company_id": cid, "name": "New Person"}).status_code == 403
    assert c.post("/api/people", json={"company_id": 999999, "name": "New Person"}).status_code == 404
    assert c.post("/api/people", json={"company_id": cid, "name": "Cher"}).status_code == 400  # needs two names
    assert c.post("/api/people", json={"company_id": cid, "name": "Contact0 Doe"}).status_code == 409
    assert c.post("/api/people", json={"company_id": cid, "name": "Ann Lee", "email": "nope"}).status_code == 400
    assert c.post("/api/people", json={"company_id": cid, "name": "Al"}).status_code == 422  # too short
    p = c.post(
        "/api/people",
        json={"company_id": cid, "name": "Ann Lee", "title": "Head of IT", "email": "ann@firm0.com"},
    )
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    assert p.json()["seniority"] == "director" and p.json()["email_status"] == "published"
    # filters
    assert c.get("/api/people", params={"q": "Ann"}).json()["total"] == 1
    assert c.get("/api/people", params={"seniority": ["director"]}).json()["total"] == 1
    assert c.get("/api/people", params={"seniority": ["partner"]}).json()["total"] == 1
    assert c.get("/api/people", params={"company_id": cid, "has_email": True}).json()["total"] == 2
    assert c.get("/api/people", params={"country": ["gb"]}).json()["total"] == 2
    assert c.get("/api/people", params={"segment": ["Law"]}).json()["total"] == 2
    assert c.get("/api/people", params={"segment": ["CPA"]}).json()["total"] == 0
    half = c.get("/api/people", params={"page_size": 1}).json()
    assert half["total"] == 2 and len(half["items"]) == 1
    assert c.get("/api/people", params={"page": 0}).status_code == 422
    # detail with provenance
    detail = c.get(f"/api/people/{pid}").json()
    assert detail["company"] == "Firm 0" and detail["company_domain"] == "firm0.com"
    assert any(o["field"] == "title" for o in detail["sources"])
    # update persists
    upd = c.patch(f"/api/people/{pid}", json={"title": "CFO"})
    assert upd.json()["seniority"] == "c_level"
    assert c.get(f"/api/people/{pid}").json()["title"] == "CFO"
    assert c.patch(f"/api/people/{pid}", json={"name": "Nope"}).status_code == 400  # one name only
    assert c.patch(f"/api/people/{pid}", json={"email": "nope"}).status_code == 400
    assert c.patch(f"/api/people/{pid}", json={"phone": "12"}).status_code == 400
    assert c.patch("/api/people/999999", json={"title": "X"}).status_code == 404
    # delete persists
    assert c.delete(f"/api/people/{pid}").json() == {"deleted": 1}
    assert c.get(f"/api/people/{pid}").status_code == 404
    assert c.delete(f"/api/people/{pid}").status_code == 404
    assert c.get("/api/people").json()["total"] == 1


# ---- runs / discovery -----------------------------------------------------------------------------
def test_runs_create_list_cancel_retry(app, db):
    c = client(app, "sdr")
    viewer = client(app, "viewer")
    made = c.post("/api/runs", json={"source": "gleif", "params": {"country": "SA", "query": "law"}})
    assert made.status_code == 200 and made.json()["status"] == "queued"
    run_id = made.json()["id"]
    # there is no GET one-run route: reads go through the list
    assert c.get(f"/api/runs/{run_id}").status_code == 404
    rows = c.get("/api/runs").json()
    assert [r["id"] for r in rows] == [run_id] and rows[0]["source"] == "gleif"
    assert c.get("/api/runs", params={"limit": 1}).json() == rows
    assert c.get("/api/runs", params={"source": ["osm"]}).json() == []
    assert {r["status"] for r in c.get("/api/runs", params={"status": ["queued"]}).json()} == {"queued"}
    # cancel a queued run; the state is persisted on re-read
    ok = c.post(f"/api/runs/{run_id}/cancel")
    assert ok.status_code == 200 and ok.json()["status"] == "cancelled"
    assert ok.json()["message"].startswith("Cancelled by")
    after = next(r for r in c.get("/api/runs").json() if r["id"] == run_id)
    assert after["status"] == "cancelled" and after["finished_at"]
    assert c.post(f"/api/runs/{run_id}/cancel").status_code == 409  # already terminal
    assert c.post("/api/runs/999999/cancel").status_code == 404
    # a running run cannot be cancelled (the worker owns it)
    r2 = c.post("/api/runs", json={"source": "gleif", "params": {"country": "SA", "query": "audit"}}).json()
    with db.session() as s:
        s.get(Run, r2["id"]).status = "running"
    assert c.post(f"/api/runs/{r2['id']}/cancel").status_code == 409
    # retry a failed run: a new queued run with the same source and params
    with db.session() as s:
        s.get(Run, r2["id"]).status = "failed"
    retry = c.post(f"/api/runs/{r2['id']}/retry")
    assert retry.status_code == 200, retry.text
    fresh = retry.json()
    assert fresh["id"] != r2["id"] and fresh["status"] == "queued"
    assert fresh["source"] == "gleif" and fresh["params"] == r2["params"]
    assert c.post("/api/runs/999999/retry").status_code == 404
    assert c.post(f"/api/runs/{fresh['id']}/retry").status_code == 409  # still queued: no duplicate work
    # the retried run is visible in the list
    assert {r["id"] for r in c.get("/api/runs").json()} == {run_id, r2["id"], fresh["id"]}
    # role rule
    assert viewer.post(f"/api/runs/{run_id}/cancel").status_code == 403
    assert viewer.post(f"/api/runs/{run_id}/retry").status_code == 403


def test_sources_catalog(app, db):
    seed(db, 1)
    c = client(app, "viewer")
    src = c.get("/api/sources").json()
    assert src and all(s["key"] and s["name"] and "available" in s and "params" in s for s in src)
    osm = next(s for s in src if s["key"] == "osm")
    assert osm["available"] is True and osm["last_run"] is None


# ---- automations ----------------------------------------------------------------------------------
def test_automations_crud_and_permissions(app, db):
    make_user(db, "sdr2@test.io", "sdr")
    c = client(app, "sdr")
    sdr2 = client(app, "sdr2")
    admin = client(app, "admin")
    viewer = client(app, "viewer")
    assert viewer.post("/api/automations", json={"name": "x", "source": "osm"}).status_code == 403
    assert c.post("/api/automations", json={"name": "x", "source": "nope"}).status_code == 400
    bad = c.post("/api/automations", json={"name": "x", "source": "osm", "rotate_field": "city", "rotate_values": []})
    assert bad.status_code == 400
    r = c.post(
        "/api/automations",
        json={
            "name": "Leeds law",
            "source": "osm",
            "params": {"segment": "Law"},
            "rotate_field": "city",
            "rotate_values": ["Leeds", "York"],
            "cadence_minutes": 30,
        },
    )
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert r.json()["next_value"] == "Leeds" and r.json()["enabled"] is True and r.json()["builtin"] is False
    assert any(a["id"] == aid for a in c.get("/api/automations").json())
    # update persists
    upd = c.patch(f"/api/automations/{aid}", json={"name": "York law", "cadence_minutes": 90, "enabled": False})
    assert upd.status_code == 200 and upd.json()["enabled"] is False and upd.json()["next_run_at"] is None
    mine = next(a for a in c.get("/api/automations").json() if a["id"] == aid)
    assert mine["name"] == "York law" and mine["cadence_minutes"] == 90
    # validation and 404s
    assert c.patch(f"/api/automations/{aid}", json={"cadence_minutes": 2}).status_code == 422
    assert c.patch(f"/api/automations/{aid}", json={"params": {"segment": "Nope"}}).status_code == 400
    assert c.patch(f"/api/automations/{aid}", json={"name": ""}).status_code == 422
    assert c.patch("/api/automations/999999", json={"name": "x"}).status_code == 404
    assert c.delete("/api/automations/999999").status_code == 404
    # run now, then refuse a second run while one is queued
    run = c.post(f"/api/automations/{aid}/run")
    assert run.status_code == 200 and run.json()["automation"] == "York law"
    assert run.json()["params"]["city"] == "Leeds"
    assert c.post(f"/api/automations/{aid}/run").status_code == 409
    assert c.post("/api/automations/999999/run").status_code == 404
    # only the creator or an admin may delete
    assert sdr2.delete(f"/api/automations/{aid}").status_code == 403
    assert admin.delete(f"/api/automations/{aid}").json() == {"deleted": 1}
    assert all(a["id"] != aid for a in c.get("/api/automations").json())
    assert c.delete(f"/api/automations/{aid}").status_code == 404
    # the queue run left behind is no longer tied to an automation
    assert c.get("/api/runs").json()[0]["automation_id"] is None


# ---- exclusions and signals -----------------------------------------------------------------------
def test_exclusions_lifecycle(app, db):
    cid = seed(db, 1)[0]
    c = client(app, "sdr")
    admin = client(app, "admin")
    viewer = client(app, "viewer")
    assert c.get("/api/exclusions").json() == []
    assert c.delete(f"/api/companies/{cid}?exclude=true").json() == {"deleted": 1}
    rows = c.get("/api/exclusions").json()
    values = {r["value"] for r in rows}
    assert values == {"domain:firm0.com", "name:GB:firm 0"}
    assert all(r["label"] == "Firm 0" and r["created_at"] for r in rows)
    eid = rows[0]["id"]
    assert viewer.get("/api/exclusions").status_code == 403
    assert viewer.delete(f"/api/exclusions/{eid}").status_code == 403
    assert c.delete(f"/api/exclusions/{eid}").status_code == 403  # sdr reads them, admin removes them
    assert admin.delete(f"/api/exclusions/{eid}").json() == {"deleted": 1}
    assert all(r["id"] != eid for r in admin.get("/api/exclusions").json())
    assert admin.delete(f"/api/exclusions/{eid}").status_code == 404
    # the other key survives and still blocks automated discovery
    assert len(admin.get("/api/exclusions").json()) == 1
    with pytest.raises(ExcludedRecord), db.session() as s:
        upsert_company(s, CompanyRecord("osm", None, "Firm 0", website="firm0.com", country="GB"))


def test_signals_feed_filters(app, db):
    cid = seed(db, 1)[0]
    with db.session() as s:
        s.add(Signal(company_id=cid, kind="hiring", title="Hiring a compliance lead", source="careers"))
        s.add(Signal(company_id=cid, kind="site_mention", title="Mentions ISO 27001", source="crawler"))
        s.add(Signal(company_id=cid, kind="news", title="Firm 0 raises funding", source="news"))
    c = client(app, "viewer")
    rows = c.get("/api/signals").json()
    assert len(rows) == 3 and {r["kind"] for r in rows} == {"hiring", "site_mention", "news"}
    assert all(r["company"] == "Firm 0" and r["company_id"] == cid for r in rows)
    hiring = c.get("/api/signals", params={"kind": ["hiring"]}).json()
    assert len(hiring) == 1 and hiring[0]["title"] == "Hiring a compliance lead"
    assert len(c.get("/api/signals", params={"q": "ISO"}).json()) == 1
    assert len(c.get("/api/signals", params={"q": "firm 0"}).json()) == 3  # matches the company name
    assert len(c.get("/api/signals", params={"limit": 2}).json()) == 2
    assert c.get("/api/signals", params={"limit": 0}).status_code == 422


# ---- tasks ----------------------------------------------------------------------------------------
def test_tasks_crud_filters_and_permissions(app, db):
    cid = seed(db, 1)[0]
    make_user(db, "sdr2@test.io", "sdr")
    sdr = client(app, "sdr")
    sdr2 = client(app, "sdr2")
    viewer = client(app, "viewer")
    assert viewer.post("/api/tasks", json={"title": "x"}).status_code == 403
    made = sdr.post(
        "/api/tasks",
        json={
            "title": "Call Firm 0",
            "kind": "call",
            "company_id": cid,
            "due_on": date.today().isoformat(),
            "priority": "high",
        },
    )
    assert made.status_code == 201, made.text
    tid = made.json()["id"]
    assert made.json()["assignee"] == "Sdr" and made.json()["overdue"] is False
    assert sdr.patch(f"/api/tasks/{tid}", json={"status": "in_progress"}).json()["status"] == "in_progress"
    # only the creator, the assignee or an admin may change a task
    assert sdr2.patch(f"/api/tasks/{tid}", json={"status": "done"}).status_code == 403
    assert sdr2.delete(f"/api/tasks/{tid}").status_code == 403
    # filters
    assert sdr.get("/api/tasks", params={"assignee": "all"}).json()["total"] == 1
    assert sdr.get("/api/tasks", params={"assignee": "me"}).json()["total"] == 1
    assert sdr.get("/api/tasks", params={"assignee": "unassigned"}).json()["total"] == 0
    assert sdr.get("/api/tasks", params={"assignee": str(sdr2.get("/api/auth/me").json()["id"])}).json()["total"] == 0
    assert sdr.get("/api/tasks", params={"status": ["in_progress"]}).json()["total"] == 1
    assert sdr.get("/api/tasks", params={"status": ["done"]}).json()["total"] == 0
    assert sdr.get("/api/tasks", params={"q": "firm 0"}).json()["total"] == 1
    assert sdr.get("/api/tasks", params={"q": "zzz"}).json()["total"] == 0
    assert sdr.get("/api/tasks", params={"company_id": cid}).json()["total"] == 1
    assert sdr.get("/api/tasks", params={"company_id": 999999}).json()["total"] == 0
    assert sdr.get("/api/tasks", params={"due": "today"}).json()["total"] == 1
    assert sdr.get("/api/tasks", params={"due": "overdue"}).json()["total"] == 0
    assert sdr.get("/api/tasks", params={"due": "none"}).json()["total"] == 0
    assert sdr.get("/api/tasks", params={"page_size": 0}).status_code == 422
    assert sdr.get("/api/tasks", params={"due": "someday"}).status_code == 422
    summary = sdr.get("/api/tasks/summary").json()
    assert summary["mine"] == 1 and summary["mine_overdue"] == 0 and summary["unassigned"] == 0
    assert summary["team"] == [{"user_id": summary["team"][0]["user_id"], "name": "Sdr", "open": 1}]
    # validation and 404s
    assert sdr.patch(f"/api/tasks/{tid}", json={"status": "nope"}).status_code == 422
    assert sdr.patch(f"/api/tasks/{tid}", json={"assignee_id": 999999}).status_code == 400
    assert sdr.post("/api/tasks", json={"title": "x", "company_id": 999999}).status_code == 404
    assert sdr.patch("/api/tasks/999999", json={"status": "done"}).status_code == 404
    assert sdr.delete("/api/tasks/999999").status_code == 404
    # the company sub-task list follows the task lifecycle
    assert len(sdr.get(f"/api/companies/{cid}/tasks").json()) == 1
    assert sdr.delete(f"/api/tasks/{tid}").json() == {"deleted": 1}
    assert sdr.get("/api/tasks", params={"assignee": "all"}).json()["total"] == 0
    assert sdr.get(f"/api/companies/{cid}/tasks").json() == []
    # bulk create for a company that does not exist creates nothing
    assert sdr.post("/api/tasks/bulk", json={"company_ids": [999999], "title": "x"}).json() == {"created": 0}


# ---- outreach (engine not connected in this fixture) ----------------------------------------------
def test_outreach_endpoints_report_a_missing_engine(app):
    sdr = client(app, "sdr")
    viewer = client(app, "viewer")
    for path in ("/api/outreach/summary", "/api/outreach/drafts", "/api/outreach/leads", "/api/outreach/linkedin"):
        assert sdr.get(path).status_code == 503, path
    assert sdr.post("/api/outreach/tick").status_code == 503
    assert sdr.post("/api/outreach/promote", json={"ids": [1]}).status_code == 503
    assert sdr.post("/api/outreach/drafts/1/decision", json={"action": "approve"}).status_code == 503
    assert sdr.post("/api/outreach/linkedin/L00001", json={"event": "sent"}).status_code == 503
    # the role rule still runs before the engine is touched
    assert viewer.post("/api/outreach/promote", json={"ids": [1]}).status_code == 403
    assert viewer.post("/api/outreach/tick").status_code == 403
    assert viewer.post("/api/outreach/drafts/1/decision", json={"action": "approve"}).status_code == 403
    # reads are allowed for a viewer; the engine is simply not there
    assert viewer.get("/api/outreach/summary").status_code == 503


# ---- plan, routine, events, KPIs, calls -----------------------------------------------------------
def test_plan_crud_and_resync(app, db):
    sdr = client(app, "sdr")
    admin = client(app, "admin")
    viewer = client(app, "viewer")
    plan = sdr.get("/api/plan").json()
    assert plan["plan_source"]["error"] is None
    assert len(plan["weeks"]) == 4 and any(w["is_current"] for w in plan["weeks"])
    task = next(t for w in plan["weeks"] for t in w["tasks"])
    upd = sdr.patch(f"/api/plan/tasks/{task['id']}", json={"status": "doing", "notes": "In progress now"})
    assert upd.status_code == 200 and upd.json()["status"] == "doing"
    again = next(t for w in sdr.get("/api/plan").json()["weeks"] for t in w["tasks"] if t["id"] == task["id"])
    assert again["status"] == "doing" and again["notes"] == "In progress now"
    assert sdr.patch(f"/api/plan/tasks/{task['id']}", json={"status": "meh"}).status_code == 422
    assert sdr.patch(f"/api/plan/tasks/{task['id']}", json={"assignee_id": 999999}).status_code == 400
    assert sdr.patch("/api/plan/tasks/999999", json={"status": "done"}).status_code == 404
    assert viewer.patch(f"/api/plan/tasks/{task['id']}", json={"status": "done"}).status_code == 403
    resync = admin.post("/api/plan/resync")
    assert resync.status_code == 200 and resync.json()["counts"]["weeks"] == 4
    assert resync.json()["plan_source"]["error"] is None


def test_routine_toggle_crud(app, db):
    sdr = client(app, "sdr")
    viewer = client(app, "viewer")
    before = sdr.get("/api/routine").json()
    assert len(before["items"]) == 4 and before["engine_available"] is False
    item = before["items"][0]
    assert item["done"] is False and item["team_done"] == []
    assert sdr.post(f"/api/routine/{item['id']}/toggle").json() == {"done": True}
    after = next(i for i in sdr.get("/api/routine").json()["items"] if i["id"] == item["id"])
    assert after["done"] is True and after["team_done"] == ["Sdr"]
    assert sdr.post(f"/api/routine/{item['id']}/toggle").json() == {"done": False}
    assert next(i for i in sdr.get("/api/routine").json()["items"] if i["id"] == item["id"])["done"] is False
    assert sdr.post("/api/routine/999999/toggle").status_code == 404
    assert viewer.post(f"/api/routine/{item['id']}/toggle").status_code == 403


def test_events_crud(app, db):
    sdr = client(app, "sdr")
    viewer = client(app, "viewer")
    events = sdr.get("/api/events").json()
    assert len(events) == 8
    e = events[0]
    assert {
        "id",
        "name",
        "city",
        "country",
        "start",
        "end",
        "segments",
        "status",
        "notes",
        "days_until",
        "is_past",
    } <= set(e)
    upd = sdr.patch(f"/api/events/{e['id']}", json={"status": "attending", "notes": "Book flights"})
    assert upd.json()["status"] == "attending" and upd.json()["notes"] == "Book flights"
    reread = next(x for x in sdr.get("/api/events").json() if x["id"] == e["id"])
    assert reread["status"] == "attending" and reread["notes"] == "Book flights"
    assert sdr.patch(f"/api/events/{e['id']}", json={"status": "nope"}).status_code == 422
    assert sdr.patch(f"/api/events/{e['id']}", json={"notes": "x" * 4001}).status_code == 422
    assert sdr.patch("/api/events/999999", json={"status": "done"}).status_code == 404
    assert viewer.patch(f"/api/events/{e['id']}", json={"status": "done"}).status_code == 403


def test_kpis_and_manual_entries(app, db):
    sdr = client(app, "sdr")
    viewer = client(app, "viewer")
    kpis = sdr.get("/api/kpis").json()
    assert {"spam_rate", "li_acceptance", "reply_time"} <= {k["key"] for k in kpis["kpis"]}
    spam = next(k for k in kpis["kpis"] if k["key"] == "spam_rate")
    assert spam["source"] == "manual" and spam["value"] is None
    assert isinstance(kpis["funnel"], list) and isinstance(kpis["entries"], list)
    # validation
    assert sdr.post("/api/kpis/spam_rate/entries", json={"value": -1, "on": "2026-10-01"}).status_code == 422
    assert sdr.post("/api/kpis/spam_rate/entries", json={"value": 1, "on": "not-a-date"}).status_code == 422
    assert sdr.post("/api/kpis/spam_rate/entries", json={"value": 1}).status_code == 422
    assert sdr.post("/api/kpis/li_acceptance/entries", json={"value": 5, "on": "2026-10-01"}).status_code == 400
    assert sdr.post("/api/kpis/no_such_kpi/entries", json={"value": 5, "on": "2026-10-01"}).status_code == 404
    # a reading persists and is used by the next evaluation
    ok = sdr.post("/api/kpis/spam_rate/entries", json={"value": 2.5, "on": "2026-10-01", "note": "Postmaster"})
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    kpis2 = sdr.get("/api/kpis").json()
    assert any(e["key"] == "spam_rate" and e["value"] == 2.5 and e["note"] == "Postmaster" for e in kpis2["entries"])
    spam2 = next(k for k in kpis2["kpis"] if k["key"] == "spam_rate")
    assert spam2["value"] == 2.5 and spam2["sample"] == 1
    assert viewer.post("/api/kpis/spam_rate/entries", json={"value": 1, "on": "2026-10-01"}).status_code == 403


def test_kpis_engine_kpi_rejects_manual_readings(app, db):
    # the plan markdown is the source of truth for KPI definitions; engine-measured KPIs are read-only
    with db.session() as s:
        s.add(
            KpiDef(
                key="test_auto_kpi",
                name="Test engine KPI",
                unit="count",
                direction="higher",
                target=1,
                source="engine",
                sort=99,
            )
        )
    c = client(app, "sdr")
    assert c.post("/api/kpis/test_auto_kpi/entries", json={"value": 1, "on": "2026-10-01"}).status_code == 400
    assert any(k["key"] == "test_auto_kpi" for k in c.get("/api/kpis").json()["kpis"])


def test_calls_crud(app, db):
    sdr = client(app, "sdr")
    viewer = client(app, "viewer")
    assert sdr.get("/api/calls").json() == []
    assert sdr.post("/api/calls", json={"company": "Acme", "outcome": "maybe"}).status_code == 422
    assert sdr.post("/api/calls", json={"company": "Acme", "package": "P99"}).status_code == 422
    assert sdr.post("/api/calls", json={"company": "Acme", "value_usd": -5}).status_code == 422
    assert sdr.post("/api/calls", json={"company": ""}).status_code == 422
    assert sdr.post("/api/calls", json={"company": "Acme", "source": "carrier-pigeon"}).status_code == 422
    made = sdr.post(
        "/api/calls",
        json={
            "company": "Acme Ltd",
            "contact": "Jane",
            "source": "referral",
            "outcome": "pilot",
            "package": "P03",
            "value_usd": 2500,
            "notes": "Pilot agreed",
        },
    )
    assert made.status_code == 200, made.text
    cid = made.json()["id"]
    assert made.json()["owner_id"] == sdr.get("/api/auth/me").json()["id"]
    rows = sdr.get("/api/calls").json()
    assert len(rows) == 1 and rows[0]["company"] == "Acme Ltd" and rows[0]["value_usd"] == 2500
    upd = sdr.patch(f"/api/calls/{cid}", json={"held": True, "outcome": "no"})
    assert upd.json()["held"] is True and upd.json()["outcome"] == "no"
    assert sdr.get("/api/calls").json()[0]["outcome"] == "no"
    assert sdr.patch(f"/api/calls/{cid}", json={"outcome": "maybe"}).status_code == 422
    assert sdr.patch("/api/calls/999999", json={"held": True}).status_code == 404
    assert viewer.post("/api/calls", json={"company": "X"}).status_code == 403
    assert viewer.patch(f"/api/calls/{cid}", json={"held": True}).status_code == 403
    # calls feed the dashboard counters
    calls = viewer.get("/api/dashboard").json()["calls"]
    assert calls["booked"] == 1 and calls["held"] == 1


# ---- users, audit, docs ---------------------------------------------------------------------------
def test_team_users_crud(app, db):
    sdr = client(app, "sdr")
    admin = client(app, "admin")
    people = sdr.get("/api/users").json()
    assert {u["email"] for u in people} == {"admin@test.io", "sdr@test.io", "viewer@test.io"}
    admin_id = next(u["id"] for u in people if u["email"] == "admin@test.io")
    weak = admin.post("/api/users", json={"email": "new@x.io", "name": "N", "role": "sdr", "password": "short"})
    assert weak.status_code == 400 and "at least 12 characters" in weak.json()["detail"]
    assert (
        admin.post(
            "/api/users", json={"email": "bad-email", "name": "N", "role": "sdr", "password": "Abcdefghij12"}
        ).status_code
        == 422
    )
    # documented: the pattern runs before the handler strips, so pasted whitespace is refused
    assert (
        admin.post(
            "/api/users", json={"email": " padded@x.io ", "name": "N", "role": "sdr", "password": "Abcdefghij12"}
        ).status_code
        == 422
    )
    assert (
        admin.post(
            "/api/users", json={"email": "new@x.io", "name": "N", "role": "boss", "password": "Abcdefghij12"}
        ).status_code
        == 422
    )
    made = admin.post(
        "/api/users", json={"email": "NEW@X.io", "name": "New Person", "role": "sdr", "password": "Abcdefghij12"}
    )
    assert made.status_code == 200 and made.json()["email"] == "new@x.io"  # stored lower-cased
    dup = admin.post("/api/users", json={"email": "new@x.io", "name": "N", "role": "sdr", "password": "Abcdefghij12"})
    assert dup.status_code == 409
    assert "new@x.io" in {u["email"] for u in sdr.get("/api/users").json()}  # persisted
    new_id = made.json()["id"]
    patched = admin.patch(f"/api/users/{new_id}", json={"role": "viewer", "active": False})
    assert patched.json()["role"] == "viewer" and patched.json()["active"] is False
    reread = next(u for u in sdr.get("/api/users").json() if u["id"] == new_id)
    assert reread["role"] == "viewer" and reread["active"] is False
    # a disabled account cannot sign in
    refused = TestClient(app).post(
        "/api/auth/login",
        json={"email": "new@x.io", "password": "Abcdefghij12"},
        headers={"X-Requested-With": "prospector"},
    )
    assert refused.status_code == 401
    # guards
    assert admin.patch(f"/api/users/{admin_id}", json={"role": "viewer"}).status_code == 400
    assert admin.patch(f"/api/users/{admin_id}", json={"active": False}).status_code == 400
    assert admin.patch("/api/users/999999", json={"role": "sdr"}).status_code == 404
    assert admin.patch(f"/api/users/{new_id}", json={"password": "weak"}).status_code == 400
    # a password change is usable immediately
    assert admin.patch(f"/api/users/{admin_id}", json={"password": "NewPassw0rd42"}).status_code == 200
    fresh = TestClient(app).post(
        "/api/auth/login",
        json={"email": "admin@test.io", "password": "NewPassw0rd42"},
        headers={"X-Requested-With": "prospector"},
    )
    assert fresh.status_code == 200


def test_audit_log_records_writes(app, db):
    sdr = client(app, "sdr")
    admin = client(app, "admin")
    viewer = client(app, "viewer")
    assert viewer.get("/api/audit").status_code == 403
    assert sdr.get("/api/audit").status_code == 403
    before = admin.get("/api/audit").json()
    assert all({"at", "user_id", "action", "target", "detail"} <= set(r) for r in before)
    created = sdr.post("/api/companies", json={"name": "Audited Co"})
    assert created.status_code == 201
    rows = admin.get("/api/audit").json()
    assert len(rows) > len(before)
    assert {"login", "company_created"} <= {r["action"] for r in rows}
    entry = next(r for r in rows if r["action"] == "company_created")
    assert entry["target"] == f"company:{created.json()['id']}" and entry["user_id"]
    assert rows == sorted(rows, key=lambda r: r["at"], reverse=True)


def test_docs_catalog_and_content(app):
    c = client(app, "viewer")
    groups = c.get("/api/docs").json()
    assert groups and all(g["group"] and g["docs"] for g in groups)
    doc = groups[0]["docs"][0]
    body = c.get("/api/docs/content", params={"path": doc["path"]}).json()
    assert body["path"] == doc["path"] and body["title"]
    assert body["markdown"].strip() and isinstance(body["headings"], list) and body["headings"]
    assert c.get("/api/docs/content", params={"path": "nope/none.md"}).status_code == 404
    assert c.get("/api/docs/content", params={"path": "../../Windows/win.ini"}).status_code == 404
    assert c.get("/api/docs/content").status_code == 422
    assert TestClient(app).get("/api/docs").status_code == 401


# ---- dashboard, quality, engine -------------------------------------------------------------------
def test_dashboard_quality_and_engine_reads(app, db, settings):
    seed(db, 2)
    viewer = client(app, "viewer")
    dash = viewer.get("/api/dashboard").json()
    # the dashboard reports "today" in the operator timezone (Asia/Karachi unless configured otherwise)
    assert dash["today"] == datetime.now(ZoneInfo(settings.operator_timezone)).date().isoformat()
    assert dash["campaign"]["start"] and isinstance(dash["campaign"]["day"], int)
    assert dash["plan_source"]["error"] is None
    assert dash["engine"]["available"] is False and dash["engine"]["error"]
    assert dash["week"] and dash["week"]["tasks"] and dash["week"]["label"]
    assert dash["kpis"] and dash["funnel"] and dash["routine"]["total"] == 4
    assert isinstance(dash["warnings"], list)
    q = viewer.get("/api/quality").json()
    assert q["checks"] and q["bands"] and q["fit_bands"] and q["tiers"]
    assert q["scored"] + q["unscored"] == 2
    assert isinstance(q["stale"], int)
    eng = viewer.get("/api/engine").json()
    assert eng["status"] == "stopped" and eng["worker_threads"] == 0  # the fixture runs no worker
    assert eng["queue"] == {"queued": 0, "running": []}
    assert eng["last_24h"]["runs"] == 0 and eng["automations_enabled"] == 0
    assert eng["series"] and eng["cache"]["version"] >= 0
    # seeding the daily snapshot made today's row readable (metrics are kept in UTC)
    assert eng["series"][-1]["day"] == datetime.now(UTC).date().isoformat()
    assert viewer.get("/api/health").json() == {"ok": True, "engine": False}
