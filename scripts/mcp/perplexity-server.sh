#!/usr/bin/env bash
# Perplexity MCP stdio server.
#
# The API key is read from 2.0/.env at launch — the same file the trading
# scripts source, so the key has exactly one source of truth. Do NOT paste the
# key into .mcp.json (git-tracked) or .claude/settings.json; rotating it should
# mean editing 2.0/.env and 5.0/.env only.
#
# stdout is the MCP transport — nothing may be printed there. Diagnostics go to
# stderr, which Claude Code surfaces in `claude mcp list` / --mcp-debug.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/2.0/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "perplexity-mcp: env file not found: $ENV_FILE" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${PERPLEXITY_API_KEY:-}" ]]; then
    echo "perplexity-mcp: PERPLEXITY_API_KEY is unset or empty in $ENV_FILE" >&2
    exit 1
fi

# Claude Code may launch this without the user's interactive shell PATH, which
# is where nvm puts node/npx. Fall back to loading nvm before giving up.
if ! command -v npx >/dev/null 2>&1; then
    if [[ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]]; then
        # shellcheck disable=SC1091
        source "${NVM_DIR:-$HOME/.nvm}/nvm.sh" >/dev/null 2>&1
    fi
fi

if ! command -v npx >/dev/null 2>&1; then
    echo "perplexity-mcp: npx not found on PATH (node/nvm not loaded)" >&2
    exit 1
fi

# Pinned. `npx -y <pkg>` with no version resolves to latest on every launch,
# so an upstream major could change tool names or arguments with no commit
# here and no warning. Bump deliberately.
exec npx -y @perplexity-ai/mcp-server@1.2.1
