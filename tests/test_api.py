from fastapi.testclient import TestClient

from prospector.resolve import CompanyRecord, upsert_company

from .conftest import login


def seed(db, n=3):
    with db.session() as s:
        for i in range(n):
            upsert_company(
                s, CompanyRecord("osm", None, f"Firm {i}", website=f"firm{i}.com", country="GB", segment="Law")
            )


def test_requires_login_and_csrf(app):
    c = TestClient(app)
    assert c.get("/api/companies").status_code == 401
    login(c, "sdr@test.io")
    assert c.get("/api/companies").status_code == 200
    r = c.post("/api/runs", json={"source": "osm", "params": {}}, headers={"X-Requested-With": ""})
    assert r.status_code == 403


def test_login_throttle(app):
    c = TestClient(app)
    for _ in range(5):
        r = c.post(
            "/api/auth/login",
            json={"email": "admin@test.io", "password": "wrong"},
            headers={"X-Requested-With": "prospector"},
        )
        assert r.status_code == 401
    r = c.post(
        "/api/auth/login",
        json={"email": "admin@test.io", "password": "wrong"},
        headers={"X-Requested-With": "prospector"},
    )
    assert r.status_code == 429


def test_roles(app):
    viewer = login(TestClient(app), "viewer@test.io")
    assert (
        viewer.post("/api/runs", json={"source": "osm", "params": {"segment": "Law", "city": "X"}}).status_code == 403
    )
    sdr = login(TestClient(app), "sdr@test.io")
    assert (
        sdr.post(
            "/api/users", json={"email": "a@b.co", "name": "A", "role": "sdr", "password": "Abcdefghij12"}
        ).status_code
        == 403
    )


def test_companies_filters_paging_and_detail(app, db):
    seed(db, 5)
    c = login(TestClient(app), "viewer@test.io")
    r = c.get("/api/companies", params={"page_size": 2, "segment": "Law"}).json()
    assert r["total"] == 5 and len(r["items"]) == 2
    assert c.get("/api/companies", params={"q": "firm 3"}).json()["total"] == 1
    assert c.get("/api/companies", params={"q": "100%_"}).json()["total"] == 0  # LIKE wildcards are escaped
    cid = r["items"][0]["id"]
    detail = c.get(f"/api/companies/{cid}").json()
    assert detail["sources"] and all(src["source"] == "osm" for src in detail["sources"])
    assert c.get("/api/companies/999999").status_code == 404


def test_bulk_status_and_run_validation(app, db):
    seed(db, 2)
    c = login(TestClient(app), "sdr@test.io")
    ids = [x["id"] for x in c.get("/api/companies").json()["items"]]
    assert c.post("/api/companies/bulk", json={"ids": ids, "action": "status", "status": "ready"}).json() == {
        "updated": 2
    }
    assert c.post("/api/runs", json={"source": "osm", "params": {"segment": "Nope", "city": "X"}}).status_code == 400
    assert c.post("/api/runs", json={"source": "unknown", "params": {}}).status_code == 400
    ok = c.post("/api/runs", json={"source": "gleif", "params": {"country": "SA", "query": "law"}})
    assert ok.status_code == 200 and ok.json()["status"] == "queued"


def test_security_headers_and_docs_whitelist(app):
    c = login(TestClient(app), "viewer@test.io")
    r = c.get("/api/companies")
    assert r.headers["X-Frame-Options"] == "DENY" and "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert c.get("/api/docs/content", params={"path": "../../Windows/win.ini"}).status_code == 404
