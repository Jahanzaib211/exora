#!/usr/bin/env bash
# Start Exora on Linux/macOS (web app + data engine in one process). Run ./scripts/setup.sh once first.
#
#   ./scripts/start.sh                       # http://127.0.0.1:8090
#   ./scripts/start.sh --host 0.0.0.0        # LAN access (set a real PROSPECTOR_SECRET_KEY first)
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/prospector ]; then
  echo "Exora is not installed yet. Run: ./scripts/setup.sh" >&2
  exit 1
fi
exec .venv/bin/prospector serve "$@"
