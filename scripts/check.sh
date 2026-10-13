#!/usr/bin/env bash
# Quality gate: run every check the way CI does. Exit code is non-zero if anything fails.
#   ./scripts/check.sh
set -uo pipefail

cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1

UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
[ -x "$UV" ] || { echo "uv not found - run ./scripts/setup.sh first." >&2; exit 1; }

failed=()
step() {
  local name="$1"; shift
  printf '\033[36m== %s\033[0m\n' "$name"
  if ! "$@"; then failed+=("$name"); printf '\033[31mFAILED: %s\033[0m\n' "$name"; fi
}

step "Python lint (ruff)"          "$UV" run --frozen --extra dev ruff check src tests scripts --no-cache
step "Python format (ruff)"        "$UV" run --frozen --extra dev ruff format --check src tests scripts --no-cache
step "Python tests (pytest)"       "$UV" run --frozen --extra dev pytest -p no:cacheprovider -q
step "Front end typecheck + build" bash -c "cd web && npm run build"

if [ "${#failed[@]}" -gt 0 ]; then
  printf '\n\033[31mFailed: %s\033[0m\n' "${failed[*]}"
  exit 1
fi
printf '\n\033[32mAll checks passed.\033[0m\n'
