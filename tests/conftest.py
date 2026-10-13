"""Offline fixtures: in-memory-ish SQLite per test, no network, no outreach engine."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prospector.api import create_app
from prospector.db import Database
from prospector.models import User
from prospector.security import hash_password
from prospector.settings import load_settings

PASSWORD = "Correct-Horse-9"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No test may touch the network: DNS and RDAP lookups return nothing unless a test injects fakes."""
    import prospector.verify as verify

    monkeypatch.setattr(verify, "_resolve", lambda domain, rtype: [])
    monkeypatch.setattr(verify, "_rdap_created", lambda domain: None)


@pytest.fixture
def settings(tmp_path):
    return load_settings(
        database_url=f"sqlite:///{(tmp_path / 'p.db').as_posix()}",
        secret_key="x" * 40,
        engine_enabled=False,
        worker_enabled=False,
        web_dist=None,
        # The example plan and docs shipped in the repository, whatever folder a local .env points at.
        docs_root=REPO,
        plan_md="playbook/PLAN.md",
        seed_csv="playbook/accounts.example.csv",
        operator_timezone="UTC",
        org_name="",
        # Hermetic security settings. `env_file=".env"` is resolved from the working directory, so
        # without these the suite quietly inherits whatever this machine happens to be running
        # with (tunnel proxy trust, cookie flags, host allowlist) and passes or fails on that
        # instead of on the code. Tests that want a non-default value opt in explicitly.
        trust_proxy=False,
        cookie_secure=False,
        cookie_same_site="lax",
        api_docs_public=False,
        allowed_hosts=[],
        metrics_enabled=False,
        metrics_token=None,
        login_ip_max_fails=30,
    )


@pytest.fixture
def db(settings):
    d = Database(settings.database_url)
    d.create_all()
    return d


def make_user(db: Database, email: str, role: str) -> None:
    with db.session() as s:
        s.add(User(email=email, name=email.split("@")[0].title(), role=role, password_hash=hash_password(PASSWORD)))


@pytest.fixture
def app(settings, db):
    make_user(db, "admin@test.io", "admin")
    make_user(db, "sdr@test.io", "sdr")
    make_user(db, "viewer@test.io", "viewer")
    return create_app(settings)


def login(client: TestClient, email: str) -> TestClient:
    r = client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}, headers={"X-Requested-With": "prospector"}
    )
    assert r.status_code == 200, r.text
    client.headers["X-Requested-With"] = "prospector"
    return client
