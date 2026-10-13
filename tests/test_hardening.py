"""Enterprise hardening: request IDs, HSTS, gated docs, host allowlist, per-address throttling.

These are all enforced only by the security middleware, so nothing else in the suite would notice
if one regressed. The per-IP counter in particular shipped dead - it was read and compared but
never appended to, so a password spray was throttled only account by account, five at a time.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from prospector.api import create_app

from .conftest import PASSWORD, login

CSRF = {"X-Requested-With": "prospector"}


def variant(settings, **overrides):
    """Build an app on the same throwaway database with *overrides* applied.

    Keeps a test that needs one non-default setting from having to re-derive the whole fixture.
    """
    return create_app(settings.model_copy(update=overrides))


def test_request_id_is_minted_and_reflected(app):
    c = TestClient(app)
    minted = c.get("/api/health").headers["X-Request-ID"]
    assert len(minted) == 32 and minted.isalnum()
    assert c.get("/api/health", headers={"X-Request-ID": "trace-42"}).headers["X-Request-ID"] == "trace-42"


def test_request_id_never_reflects_a_forged_header(app):
    # A CRLF in a reflected header is response splitting, and letting callers fix their own trace
    # ID is only safe while the value is checked against a whitelist first.
    forged = TestClient(app).get("/api/health", headers={"X-Request-ID": "a\r\nX-Injected: yes"})
    assert "\r" not in forged.headers["X-Request-ID"]
    assert "\n" not in forged.headers["X-Request-ID"]
    assert "X-Injected" not in forged.headers


def test_hsts_is_sent_only_when_a_proxy_declares_https(db, settings):
    c = TestClient(variant(settings, trust_proxy=True, scheduler_enabled=False))
    assert "Strict-Transport-Security" not in c.get("/api/health").headers
    r = c.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert r.headers["Strict-Transport-Security"].startswith("max-age=")


def test_hsts_is_not_taken_from_an_untrusted_client(db, settings):
    # Without trust_proxy the forwarding header is attacker-supplied noise; honouring it would let
    # anyone pin the host over plain HTTP.
    c = TestClient(variant(settings, trust_proxy=False, scheduler_enabled=False))
    r = c.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" not in r.headers


def test_api_docs_require_an_admin_session(app):
    c = TestClient(app)
    assert c.get("/api/openapi.json").status_code == 404
    assert c.get("/api/swagger").status_code == 404
    # 404 rather than 401: an unauthenticated caller should not learn the schema exists at all.
    sdr = login(TestClient(app), "sdr@test.io")
    assert sdr.get("/api/swagger").status_code == 404
    admin = login(TestClient(app), "admin@test.io")
    assert admin.get("/api/openapi.json").status_code == 200
    assert admin.get("/api/swagger").status_code == 200


def test_api_docs_can_be_published_deliberately(db, settings):
    c = TestClient(variant(settings, api_docs_public=True, scheduler_enabled=False))
    assert c.get("/api/openapi.json").status_code == 200


def test_host_allowlist_is_off_by_default(app):
    assert TestClient(app).get("/api/health", headers={"Host": "anything.invalid"}).status_code == 200


def test_host_allowlist_answers_only_listed_hosts(db, settings):
    c = TestClient(variant(settings, allowed_hosts=["app.example.com"], scheduler_enabled=False))
    assert c.get("/api/health", headers={"Host": "app.example.com"}).status_code == 200
    bad = c.get("/api/health", headers={"Host": "evil.example.com"})
    assert bad.status_code == 400
    assert "X-Request-ID" in bad.headers  # still identifiable in the logs


def test_per_address_login_throttle(db, settings):
    """A spray across many accounts must be stopped by the address budget, not one at a time.

    Each address here uses a different email, so no account ever reaches its own limit of five:
    only the per-address counter can produce the 429. That is the regression this guards - the
    counter was never appended to, so it never fired and a spray sailed through.
    """
    c = TestClient(variant(settings, login_ip_max_fails=3, trust_proxy=True, scheduler_enabled=False))
    for i in range(3):
        r = c.post("/api/auth/login", json={"email": f"spray{i}@test.io", "password": "nope"}, headers=CSRF)
        assert r.status_code == 401, r.text
    throttled = c.post("/api/auth/login", json={"email": "last@test.io", "password": "nope"}, headers=CSRF)
    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) >= 1

    # The budget belongs to the address, so someone else is still able to attempt.
    elsewhere = c.post(
        "/api/auth/login",
        json={"email": "elsewhere@test.io", "password": "nope"},
        headers={**CSRF, "CF-Connecting-IP": "198.51.100.9"},
    )
    assert elsewhere.status_code == 401


def test_session_cookie_is_httponly_and_lax(app):
    r = TestClient(app).post("/api/auth/login", json={"email": "viewer@test.io", "password": PASSWORD}, headers=CSRF)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "; Secure" not in cookie  # tests run over plain HTTP
