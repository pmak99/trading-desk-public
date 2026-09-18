"""Shared module for Trading Desk: the engine (2.0/) and the repo-root scripts.

Database access, constants, the timezone helper, the Postgres test fixtures and
the weekly-options filter. Domain enums live in 2.0/src/domain/enums.py.

An installed package (the repo root's pyproject.toml), not a directory on
sys.path: `2.0/venv/bin/pip install --no-deps -e . -e 2.0/` from the repo root.
Every engine layer may import it, and it imports nothing from the engine -- an
import-linter contract in that pyproject.toml fails if it does.
"""
