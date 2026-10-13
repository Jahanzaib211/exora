"""Settings from environment variables (prefix PROSPECTOR_) or ./.env.

Relative paths (database, docs, web build, logs) resolve against the working directory, which is the
repository root for every supported way of running the app (scripts/, pm2, systemd, Docker).

The engine is configured separately (its own config.yaml/.env); ENGINE_CONFIG points at it.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET = "change-me-prospector-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PROSPECTOR_", env_file=".env", env_file_encoding="utf-8-sig", extra="ignore"
    )

    database_url: str = "sqlite:///data/prospector.db"
    # Secret for signing session tokens. Must be changed before anyone else can reach the app.
    secret_key: str = INSECURE_SECRET
    session_hours: int = 12
    # Set true when the app is served over HTTPS (Coolify, Cloudflare Tunnel): cookies are then HTTPS-only.
    cookie_secure: bool = False
    # SameSite for the session cookie. `lax` still refuses to send the cookie on cross-site POSTs (which
    # additionally need the X-Requested-With header), so CSRF protection is unchanged, but it *does* let a
    # top-level link from another site carry the cookie. `strict` bounced every signed-in user who followed
    # a link into the app back to the login page.
    cookie_same_site: str = "lax"
    # Honour X-Forwarded-Proto / X-Forwarded-For. Set when sitting behind Cloudflare Tunnel or any other
    # reverse proxy: without it every request looks like it came from 127.0.0.1 over plain HTTP, so the
    # per-IP throttle cannot see the real client and HSTS is never sent. Only enable when the proxy is the
    # only route in - a client that can reach the app directly can forge these headers.
    trust_proxy: bool = False
    # Serve /api/swagger and /api/openapi.json without an admin session (they enumerate every route,
    # parameter and model). Off by default; the schema is an unauthenticated API map otherwise.
    api_docs_public: bool = False
    # Empty = accept any Host header (the development and TestClient default). Populated, only the listed
    # hostnames are answered - a poisoned Host can otherwise reach a cache or an absolute-URL builder.
    allowed_hosts: list[str] = []
    # Expose /api/metrics in Prometheus text format. Off by default; when on it requires an admin session
    # or, for scrapers, a bearer token matching metrics_token.
    metrics_enabled: bool = False
    metrics_token: str | None = None
    # Per-IP login throttle across the same window as the per-account one. A distributed password spray
    # never trips a single account's limit of 5, so the source address needs its own budget.
    login_ip_max_fails: int = 30
    # IANA name, e.g. Europe/London. "Today", week boundaries and every timestamp in the UI use it.
    operator_timezone: str = "UTC"
    # Shown under the product name in the sidebar and on the sign-in page. Empty = hidden.
    org_name: str = ""
    # Discovery's User-Agent comes from PROSPECTOR_CONTACT_EMAIL / PROSPECTOR_USER_AGENT, read straight from
    # the environment by connectors.base.user_agent() because the crawler runs in a child process.
    # Folder whose markdown is the source of truth: the plan is synced from plan_md and the Docs section
    # serves the files matched by docs.DOC_GROUPS (read only).
    docs_root: Path = Path(".")
    plan_md: str = "playbook/PLAN.md"
    # CSV imported by the "Target accounts sheet" source, relative to docs_root.
    seed_csv: str = "playbook/accounts.example.csv"
    # Built front end served at /. Not built yet (or empty) = API only.
    web_dist: Path | None = Path("web/dist")
    # Outreach engine (optional, separate package): config file and whether to read its database.
    engine_enabled: bool = False
    engine_config: Path | None = None
    # Run the discovery workers and scheduler inside the web process (laptop). On a server they can run as a
    # separate `prospector worker` process instead.
    worker_enabled: bool = True
    worker_threads: int = 2
    scheduler_enabled: bool = True
    # Queue a website crawl for every new company that has a website.
    auto_enrich: bool = True
    # Windows: ask the OS not to sleep while the data engine runs (released when the app stops).
    keep_awake: bool = True
    log_dir: Path = Path("data/logs")
    export_max_rows: int = 50_000
    # Optional source keys
    companies_house_key: str | None = None

    @property
    def plan_path(self) -> Path:
        return Path(self.docs_root) / self.plan_md

    @property
    def secret_is_default(self) -> bool:
        return self.secret_key == INSECURE_SECRET or len(self.secret_key) < 32


def load_settings(**overrides) -> Settings:
    return Settings(**overrides)
