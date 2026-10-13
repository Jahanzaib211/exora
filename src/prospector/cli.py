"""Exora CLI: first-time setup, create users, run the web app, or run the data engine on its own."""

from __future__ import annotations

import logging
import os
import secrets
import signal
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

import typer
from dotenv import load_dotenv
from sqlalchemy import func, select

from .branding import BRAND, __version__
from .db import Database, audit
from .models import Role, User
from .security import hash_password, password_problems
from .settings import Settings, load_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help=f"{BRAND}: lead discovery and outreach.")


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", help="Print the version and exit.", is_eager=True),
) -> None:
    if version:
        typer.echo(f"{BRAND} {__version__}")
        raise typer.Exit()
    # Settings read ./.env themselves, but several optional integrations (GitHub token, SEC contact, SMTP
    # probe, crawler User-Agent) read os.environ, and the crawler runs as a child process. Exporting the
    # file once here makes every documented variable work from .env. Real environment variables win.
    load_dotenv(Path(".env"), override=False, encoding="utf-8-sig")


def setup_logging(settings: Settings, name: str) -> Path:
    """Console plus a rotating file (10 MB x 5) so a 24/7 engine never fills the disk."""
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{name}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers[:] = [file_handler, console]
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "scrapy", "twisted"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return path


def ensure_secret(settings: Settings) -> Settings:
    """Without PROSPECTOR_SECRET_KEY, use (and on first start create) the file PROSPECTOR_SECRET_KEY_FILE names.

    The container image sets that to a file on the data volume, so `docker compose up` needs no manual
    secret and sessions survive restarts and upgrades. An explicit PROSPECTOR_SECRET_KEY always wins.
    """
    path = os.environ.get("PROSPECTOR_SECRET_KEY_FILE", "").strip()
    if not settings.secret_is_default or not path:
        return settings
    secret_file = Path(path)
    if not secret_file.is_file():
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(secrets.token_hex(32), encoding="utf-8")
        secret_file.chmod(0o600)
        typer.echo(f"Generated a session secret in {secret_file}")
    settings.secret_key = secret_file.read_text(encoding="utf-8").strip()
    return settings


def _add_user(settings: Settings, email: str, name: str, role: str, password: str) -> None:
    if role not in Role.ALL:
        raise typer.BadParameter(f"role must be one of {Role.ALL}")
    problems = password_problems(password)
    if problems:
        raise typer.BadParameter("password: " + ", ".join(problems))
    db = Database(settings.database_url)
    db.create_all()
    with db.session() as s:
        email = email.strip().lower()
        if s.scalar(select(User).where(User.email == email)):
            typer.echo(f"{email} already exists")
            raise typer.Exit(1)
        s.add(User(email=email, name=name, role=role, password_hash=hash_password(password)))
        audit(s, "user_created", None, target=email, role=role, via="cli")
    typer.echo(f"Created {role} {email}")


@app.command("create-user")
def create_user(
    email: str = typer.Option(..., prompt=True),
    name: str = typer.Option(..., prompt=True),
    role: str = typer.Option(Role.ADMIN, help="admin | sdr | viewer"),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
) -> None:
    """Create a user (the first one should be an admin)."""
    _add_user(load_settings(), email, name, role, password)


@app.command()
def init(
    admin_email: str = typer.Option(None, help="Create this admin if the database has no users yet."),
    admin_name: str = typer.Option(None),
    admin_password: str = typer.Option(None, help="Prompted for (hidden) when omitted."),
    no_admin: bool = typer.Option(False, "--no-admin", help="Skip the first-admin step."),
) -> None:
    """First-time setup, safe to re-run: .env with a random secret, the data folder, the first admin."""
    env = Path(".env")
    if env.exists():
        typer.echo(".env already exists - left as it is.")
    else:
        example = Path(".env.example")
        text = example.read_text(encoding="utf-8") if example.is_file() else "PROSPECTOR_SECRET_KEY=\n"
        lines = [
            f"PROSPECTOR_SECRET_KEY={secrets.token_hex(32)}" if ln.startswith("PROSPECTOR_SECRET_KEY=") else ln
            for ln in text.splitlines()
        ]
        env.write_text("\n".join(lines) + "\n", encoding="utf-8")
        load_dotenv(env, override=False, encoding="utf-8-sig")
        typer.echo("Created .env with a new random PROSPECTOR_SECRET_KEY.")
    settings = load_settings()
    if settings.database_url.startswith("sqlite:///"):
        Path(settings.database_url[len("sqlite:///") :]).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_url)
    db.create_all()
    with db.session() as s:
        users = s.scalar(select(func.count()).select_from(User)) or 0
    if users:
        typer.echo(f"Database ready ({users} user{'s' if users != 1 else ''}).")
    elif no_admin:
        typer.echo("Database ready. Create the first admin later with: prospector create-user")
    else:
        typer.echo("Create the first admin account:")
        email = admin_email or typer.prompt("Email")
        name = admin_name or typer.prompt("Name")
        password = admin_password or typer.prompt(
            "Password (12+ characters, upper and lower case, a digit)", hide_input=True, confirmation_prompt=True
        )
        _add_user(settings, email, name, Role.ADMIN, password)
    typer.echo(f"Done. Start {BRAND} with: prospector serve  (then open http://127.0.0.1:8090)")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="0.0.0.0 to let teammates on your network connect"),
    port: int = typer.Option(8090),
) -> None:
    """Run the web app (API + front end). Also runs the data engine unless PROSPECTOR_WORKER_ENABLED=false."""
    import uvicorn

    from .api import create_app

    settings = ensure_secret(load_settings())
    log_path = setup_logging(settings, "exora")
    if settings.secret_is_default:
        typer.secho("WARNING: PROSPECTOR_SECRET_KEY is not set; sessions use a default key.", fg=typer.colors.RED)
        if host not in ("127.0.0.1", "localhost", "::1"):
            typer.secho(
                "Refusing to listen on the network with the default secret. Run `prospector init` or set "
                "PROSPECTOR_SECRET_KEY (32+ characters) in .env.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(2)
    typer.echo(f"{BRAND} on http://{host}:{port} · logs: {log_path}")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="info", log_config=None)


@app.command()
def worker() -> None:
    """Run only the data engine (workers + scheduler), e.g. as a separate service on a server."""
    from .discovery import Worker
    from .scheduler import Scheduler

    settings = load_settings()
    log_path = setup_logging(settings, "exora-worker")
    db = Database(settings.database_url)
    db.create_all()
    w = Worker(db, settings)
    sch = Scheduler(
        db, keep_awake=settings.keep_awake, outreach_enabled=bool(settings.engine_enabled and settings.engine_config)
    )
    w.start()
    sch.start()
    typer.echo(f"{BRAND} data engine running ({w.threads} workers). Logs: {log_path}. Ctrl+C to stop.")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.wait(1):
        pass
    sch.stop()
    w.stop()


if __name__ == "__main__":
    app()
