#!/usr/bin/env bash
# Shared path resolution for the MCP stdio wrappers. Sourced, never executed.
#
# .mcp.json launches every wrapper by relative path, so when Claude Code runs
# from a git worktree (.claude/worktrees/*) a wrapper's own location resolves to
# the worktree. But 2.0/.env, 2.0/venv and 2.0/data are all gitignored and exist
# only in the main checkout, so every wrapper needs the main checkout's root
# rather than its own — for secrets, the interpreter, and the databases alike.
#
# Getting this wrong is expensive to diagnose: a wrapper that dies before it
# speaks MCP surfaces only as an opaque CONNECTION_CLOSED in `claude mcp list`,
# with the real reason buried in stderr. Hence one implementation, not four.

# Echo the main checkout root for the repo containing $1. Falls back to $1
# unchanged when it is not a git checkout at all (e.g. a plain copy deploy),
# which preserves the original single-checkout behaviour.
mcp_main_checkout_root() {
    local repo_root="$1" common_dir

    if ! common_dir="$(git -C "$repo_root" rev-parse --git-common-dir 2>/dev/null)"; then
        echo "$repo_root"
        return 0
    fi

    # --git-common-dir is absolute when called from a worktree but relative
    # (".git") from the main checkout; only the latter needs repo_root prepended.
    case "$common_dir" in
        /*) ;;
        *) common_dir="$repo_root/$common_dir" ;;
    esac

    ( cd "$common_dir/.." && pwd )
}
