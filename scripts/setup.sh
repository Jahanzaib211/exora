#!/usr/bin/env bash
# Exora one-time setup for Linux and macOS: dependencies, web build, .env with a random secret, first admin.
# Safe to re-run (it skips what is already done).
#
#   ./scripts/setup.sh              # install and configure
#   ./scripts/setup.sh --dev        # also install test/lint tools
#   ./scripts/setup.sh --no-admin   # skip the first-admin prompt
set -euo pipefail

cd "$(dirname "$0")/.."

DEV=0
NO_ADMIN=0
for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
    --no-admin) NO_ADMIN=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

info() { printf '\033[36m==> %s\033[0m\n' "$1"; }
fail() { printf '\033[31mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

# 1. uv: installs Python 3.12 and every Python dependency from uv.lock (no system Python needed).
UV="$(command -v uv || true)"
if [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"; fi
if [ -z "$UV" ]; then
  info "Installing uv (Python package manager, https://docs.astral.sh/uv/)"
  command -v curl >/dev/null || fail "curl is needed to install uv (e.g. sudo apt install curl)."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
  [ -x "$UV" ] || fail "uv did not install. Install it from https://docs.astral.sh/uv/ and re-run."
fi

# 2. Node.js builds the web app once; it is not needed to run Exora afterwards.
command -v node >/dev/null || fail "Node.js 24 is needed to build the web app. Install it from https://nodejs.org (or with nvm: nvm install 24) and re-run."
NODE_VERSION="$(node --version)"
NODE_MAJOR="${NODE_VERSION#v}"; NODE_MAJOR="${NODE_MAJOR%%.*}"
[ "$NODE_MAJOR" -ge 24 ] || fail "Node.js 24 or newer is needed (found $NODE_VERSION)."
[ "$NODE_MAJOR" -eq 24 ] || echo "Note: Exora is tested on Node.js 24; found $NODE_VERSION."

# 3. Python environment (.venv) from the lockfile.
info "Installing Python dependencies"
if [ "$DEV" -eq 1 ]; then "$UV" sync --frozen --extra dev; else "$UV" sync --frozen; fi

# 4. Web app -> web/dist (served by the Python app).
info "Building the web app"
(cd web && npm ci --no-audit --no-fund && npm run build)

# 5. .env, database and the first admin.
info "Configuring Exora"
if [ "$NO_ADMIN" -eq 1 ]; then "$UV" run --frozen prospector init --no-admin; else "$UV" run --frozen prospector init; fi

printf '\n\033[32mExora is ready.\033[0m Start it with:\n  ./scripts/start.sh\nthen open http://127.0.0.1:8090\n'
