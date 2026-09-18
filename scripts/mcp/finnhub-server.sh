#!/usr/bin/env bash
# Finnhub MCP stdio server launcher.
#
# Runs on 2.0/venv (which owns the mcp SDK and the FinnhubAPI client) with cwd
# set to 2.0/, the directory every 2.0 entry point assumes. The database comes
# from PG* in 2.0/.env; no path is resolved against the working directory.
#
# stdout is the MCP transport — diagnostics go to stderr only.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/mcp/repo-root.sh
source "$SCRIPT_DIR/repo-root.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 2.0/.env, 2.0/venv and 2.0/data are all gitignored, so they live only in the
# main checkout (see repo-root.sh). The server code itself still comes from
# REPO_ROOT, so a worktree tests its own changes against the shared venv and DB.
SHARED_ROOT="$(mcp_main_checkout_root "$REPO_ROOT")"

ENV_FILE="$SHARED_ROOT/2.0/.env"
VENV_PY="$SHARED_ROOT/2.0/venv/bin/python"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "finnhub-mcp: env file not found: $ENV_FILE" >&2
    exit 1
fi

if [[ ! -x "$VENV_PY" ]]; then
    echo "finnhub-mcp: interpreter not found: $VENV_PY" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${FINNHUB_API_KEY:-}" ]]; then
    echo "finnhub-mcp: FINNHUB_API_KEY is unset or empty in $ENV_FILE" >&2
    exit 1
fi

# SHARED_ROOT, not REPO_ROOT: 2.0/.env and the venv exist only in the main
# checkout (see repo-root.sh).
cd "$SHARED_ROOT/2.0"
exec "$VENV_PY" "$REPO_ROOT/scripts/mcp/finnhub_server.py"
