"""Shared module for Trading Desk subsystems.

Canonical definitions for enums, constants, and utilities shared across
2.0, 4.0, 5.0, and 6.0. Each subsystem imports from here to eliminate
code duplication.

Path setup: Each subsystem's conftest.py adds the Trading Desk root
to sys.path so 'from common.x import y' works. For production code,
re-export modules handle path setup inline.
"""
