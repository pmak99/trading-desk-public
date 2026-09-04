#!/usr/bin/env bash
# Finnhub MCP stdio server launcher.
#
# Runs on 2.0/venv (which owns the mcp SDK and the FinnhubAPI client) with cwd
# set to 2.0/, because Config.from_env() resolves DB_PATH=data/ivcrush.db
# relative to the working directory.
#
# stdout is the MCP transport — diagnostics go to stderr only.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/2.0/.env"
VENV_PY="$REPO_ROOT/2.0/venv/bin/python"

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

cd "$REPO_ROOT/2.0"
exec "$VENV_PY" "$REPO_ROOT/scripts/mcp/finnhub_server.py"
