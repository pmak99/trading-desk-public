#!/usr/bin/env bash
# Knowledge-graph memory MCP stdio server.
#
# MEMORY_FILE_PATH must be absolute: the server resolves a relative path against
# its own package directory inside the npx cache, not the project — verified, a
# relative path silently writes into the cache and is lost on the next npx
# refresh. This wrapper derives the repo root so the graph lands in
# 2.0/data/ (gitignored) regardless of where the repo is checked out.
#
# stdout is the MCP transport — diagnostics go to stderr only.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export MEMORY_FILE_PATH="$REPO_ROOT/2.0/data/memory-graph.json"

if ! command -v npx >/dev/null 2>&1; then
    if [[ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]]; then
        # shellcheck disable=SC1091
        source "${NVM_DIR:-$HOME/.nvm}/nvm.sh" >/dev/null 2>&1
    fi
fi

if ! command -v npx >/dev/null 2>&1; then
    echo "memory-mcp: npx not found on PATH (node/nvm not loaded)" >&2
    exit 1
fi

# Pinned — see the note in perplexity-server.sh. Bump deliberately.
exec npx -y @modelcontextprotocol/server-memory@2026.7.4
