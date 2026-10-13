# Exora: data engine + team app in one image.
#
#   docker compose up -d --build                     # see docker-compose.yml
#   docker build -t exora .                          # image only
#
# Two stages: Node builds web/dist, Python 3.12 runs the app as an unprivileged user. The runtime image has
# no Node, no compilers and no test dependencies. Python dependencies are installed from uv.lock, so the
# image gets exactly the versions CI tested.

# ---- stage 1: front end -------------------------------------------------------------------------------
FROM node:24-bookworm-slim AS web

WORKDIR /web
# npm ci needs the lockfile before any source copy, so dependency layers stay cached across code edits.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web/ ./
# `npm run build` = check:data (no hardcoded numbers) && tsc --noEmit && vite build -> /web/dist.
RUN npm run build

# ---- stage 2: runtime ---------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PROSPECTOR_DOCS_ROOT=/app \
    PROSPECTOR_WEB_DIST=/app/web/dist \
    PROSPECTOR_DATABASE_URL=sqlite:////app/data/prospector.db \
    PROSPECTOR_LOG_DIR=/app/data/logs \
    PROSPECTOR_SECRET_KEY_FILE=/app/data/secret_key \
    PROSPECTOR_KEEP_AWAKE=false

# curl is only used by HEALTHCHECK; tini reaps the uvicorn/worker child processes on SIGTERM.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 exora

WORKDIR /app

# Dependencies on their own layer: only a pyproject/uv.lock change re-runs this.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --extra postgres --no-editable

# Markdown the app reads at runtime: the plan, the example seed sheet and the Docs section.
COPY --chown=exora:exora playbook ./playbook
COPY --chown=exora:exora docs ./docs
COPY --chown=exora:exora scripts/backup.py ./scripts/backup.py
COPY --from=web --chown=exora:exora /web/dist ./web/dist

RUN mkdir -p /app/data/logs && chown -R exora:exora /app/data
USER exora
VOLUME ["/app/data"]

EXPOSE 8090

# /api/health is public and cheap; it reports whether the outreach bridge is attached.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8090/api/health || exit 1

ENTRYPOINT ["tini", "--"]
# --host 0.0.0.0 so the container port is reachable. The session secret comes from PROSPECTOR_SECRET_KEY or,
# when unset, from /app/data/secret_key (created on first start). Publish the port on loopback or put the
# app behind a reverse proxy with HTTPS (Coolify, Caddy, Cloudflare Tunnel); see docs/DEPLOYMENT.md.
CMD ["prospector", "serve", "--host", "0.0.0.0", "--port", "8090"]
