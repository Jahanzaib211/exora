# Exora deployment

How to install, run, upgrade, back up and restore Exora on a laptop or a server. Pick one of:

| Where | How | Section |
|---|---|---|
| Windows laptop or PC | `scripts\setup.ps1` + `scripts\start.ps1` (or pm2) | [1](#1-windows) |
| Linux or macOS machine | `scripts/setup.sh` + `scripts/start.sh` (or systemd / pm2) | [2](#2-linux-and-macos) |
| Any server with Docker | `docker compose up -d --build` | [3](#3-server-with-docker-compose) |
| Coolify | Dockerfile build pack | [4](#4-coolify) |

Every option runs the same process: the web app, the REST API and the data engine (discovery workers and scheduler) on port 8090.

## 1. Windows

Prerequisites: [Git](https://git-scm.com/download/win) and [Node.js 24](https://nodejs.org). Python is not needed; uv installs it.

```powershell
git clone https://github.com/Jahanzaib211/exora.git
cd exora
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1     # once
powershell -ExecutionPolicy Bypass -File scripts\start.ps1     # http://127.0.0.1:8090
```

`setup.ps1` installs uv if missing, creates `.venv` from `uv.lock`, builds `web/dist`, writes `.env` with a random `PROSPECTOR_SECRET_KEY` and creates the first admin. It is safe to re-run.

**Keep it running** (survives closing the terminal, restarts on crash):

```powershell
npm install -g pm2
pm2.cmd start ecosystem.config.cjs
pm2.cmd save
```

On a laptop, `PROSPECTOR_KEEP_AWAKE=true` (the default) stops Windows sleeping while the data engine works.

**Team access on your network:** `scripts\start.ps1 -BindHost 0.0.0.0`, and allow port 8090 in Windows Defender Firewall for private networks only.

## 2. Linux and macOS

Prerequisites: `git`, `curl` and Node.js 24 (from [nodejs.org](https://nodejs.org), your package manager or `nvm install 24`).

```bash
git clone https://github.com/Jahanzaib211/exora.git
cd exora
./scripts/setup.sh       # once
./scripts/start.sh       # http://127.0.0.1:8090
```

**As a service on a Linux server (systemd):**

```bash
sudo useradd --system --create-home --home-dir /opt/exora exora
sudo -u exora git clone https://github.com/Jahanzaib211/exora.git /opt/exora/app
cd /opt/exora/app && sudo -u exora ./scripts/setup.sh
sudo cp deploy/exora.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now exora
journalctl -u exora -f
```

Then put a reverse proxy with HTTPS in front. With [Caddy](https://caddyserver.com) this is the whole config:

```
exora.your-company.com {
    reverse_proxy 127.0.0.1:8090
}
```

and in `.env`: `PROSPECTOR_COOKIE_SECURE=true`, `PROSPECTOR_TRUST_PROXY=true`, `PROSPECTOR_ALLOWED_HOSTS=["exora.your-company.com"]`.

## 3. Server with Docker Compose

Prerequisites: Docker with the Compose plugin (Docker Desktop on Windows/macOS).

```bash
git clone https://github.com/Jahanzaib211/exora.git
cd exora
docker compose up -d --build
docker compose exec app prospector create-user --role admin
docker compose ps        # app should become "healthy"
```

- **No `.env` required.** Without `PROSPECTOR_SECRET_KEY`, the container generates one at `/app/data/secret_key` on the `exora-data` volume, so sessions survive restarts and upgrades. To configure anything else, `cp .env.example .env`, edit, and `docker compose up -d`.
- **Port.** Published on `127.0.0.1:8090` only. Put a reverse proxy with HTTPS in front, or set `EXORA_BIND=0.0.0.0` in `.env` only on a trusted network.
- **Cloudflare Tunnel** instead of a proxy: set `CF_TUNNEL_TOKEN` in `.env` and run `docker compose --profile tunnel up -d`.
- **Separate data engine:** set `PROSPECTOR_WORKER_ENABLED=false` and `PROSPECTOR_SCHEDULER_ENABLED=false` in `.env`, then `docker compose --profile worker up -d`. With SQLite run one worker; for more, move to Postgres (section 6).
- **Logs:** `docker compose logs -f app`; files also rotate under `/app/data/logs` on the volume.

## 4. Coolify

1. **New resource → Public/Private repository** → this repository, build pack **Dockerfile** (`Dockerfile` at the repository root, build context `/`).
2. **Port** `8090`, **health check path** `/api/health`.
3. **Persistent storage:** mount a volume at `/app/data` (database, logs and the generated secret live there).
4. **Environment:** at least `PROSPECTOR_COOKIE_SECURE=true`, `PROSPECTOR_TRUST_PROXY=true` and `PROSPECTOR_ALLOWED_HOSTS=["your.domain"]`, plus anything else from `.env.example`. `PROSPECTOR_SECRET_KEY` is optional (generated on the volume when unset).
5. Attach your domain (Coolify issues the certificate), deploy, then open the terminal and run `prospector create-user --role admin`.
6. Run **one** instance with SQLite. Scale only after moving to Postgres (section 6).

## 5. Upgrading

```bash
git pull
./scripts/setup.sh --no-admin              # Windows: scripts\setup.ps1 -NoAdmin
# then restart: scripts/start.sh, `pm2 restart exora` or `sudo systemctl restart exora`
```

Docker: `git pull && docker compose up -d --build`. The schema migrates itself on start (section 6). Take a backup first (section 7).

## 6. Database

The app owns its schema. Every entry point (`serve`, `worker`, `init`, `create-user`, the tests) opens the database through `Database`, which runs `create_all()` plus an additive migration that adds new tables and nullable columns in place. There is no manual migration step.

| Deployment | Store | Why |
|---|---|---|
| Laptop, single server | SQLite (WAL), the default | Zero operations; WAL keeps readers unblocked during writes |
| Several app or worker replicas | Postgres | A SQLite file cannot be shared across machines |

Postgres: `uv sync --extra postgres` (the Docker image already includes the driver), then
`PROSPECTOR_DATABASE_URL=postgresql+psycopg://user:password@host:5432/exora`. Runs are claimed with `FOR UPDATE SKIP LOCKED`, so workers never process the same queue entry twice.

## 7. Backups and restore

SQLite runs in WAL mode, so recent commits live in `prospector.db-wal`; copying the `.db` file alone can produce a torn backup. Use the snapshot script, which calls `VACUUM INTO` for a consistent copy with no downtime:

```bash
uv run python scripts/backup.py              # -> data/backups/prospector-<timestamp>.db
uv run python scripts/backup.py --keep 14    # prune, keeping the newest 14
```

Docker: `docker compose exec app python scripts/backup.py --keep 14` writes to `/app/data/backups/` on the volume; copy one out with `docker compose cp app:/app/data/backups/<file>.db .`

Restore: stop the app, replace `data/prospector.db` with the backup, delete `data/prospector.db-wal` and `data/prospector.db-shm` if present, start the app.

Postgres: `pg_dump --format=custom --file exora-$(date +%F).dump "$DATABASE_URL"`.

Schedule backups with cron or Windows Task Scheduler and keep encrypted copies off the machine: the database contains personal data.

## 8. Optional: the outreach engine

The Outreach screens (sequences, draft approvals, LinkedIn task lists, sent and reply counts) talk to a separate outreach engine package (`outreach_engine`). Without it Exora runs normally and those screens say the engine is not configured. To connect one:

```bash
uv pip install -e /path/to/outreach-engine                   # into Exora's .venv
```

then in `.env`:

```
PROSPECTOR_ENGINE_ENABLED=true
PROSPECTOR_ENGINE_CONFIG=/path/to/outreach-engine/config.yaml
```

and restart. `/api/health` reports `"engine": true` once the bridge is attached. `uv sync` (and so the setup script) removes packages that are not in `uv.lock`, so repeat the `uv pip install` after each upgrade.

## 9. Checks before a deploy

| Check | Command |
|---|---|
| Everything below | `scripts/check.sh` (Windows: `scripts\check.ps1`) |
| Lint and format | `uv run --extra dev ruff check src tests` and `ruff format --check` |
| Backend tests | `uv run --extra dev pytest` |
| Front end | `cd web && npm run build` (data guard, typecheck, production build) |
| Browser tests | `cd web && npx playwright test` against a running server (see `playwright.config.ts`) |

CI runs all of these on Ubuntu and Windows, plus a fresh-clone setup, a Docker build and a health check, on every push.

## 10. Operating notes

- **Logs:** `data/logs/exora.log` (and `exora-worker.log`), rotating at 10 MB × 5 so a 24/7 engine never fills the disk.
- **Secrets:** `.env` is gitignored. Rotate `PROSPECTOR_SECRET_KEY` to sign everyone out.
- **Identify yourself:** set `PROSPECTOR_CONTACT_EMAIL` so the sites and registries Exora reads can contact you, and SEC EDGAR accepts its requests.
- **Never** publish port 8090 directly to the internet.
