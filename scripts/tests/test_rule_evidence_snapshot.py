"""The snapshot's band vocabulary is rule_evidence.py's, not a second copy.

rule_evidence.py owns the band definitions; refresh_rule_evidence.py publishes
them; four panels read the published labels. A band renamed in rule_evidence
without a refresh leaves those panels filtering on labels that no longer exist
and rendering empty -- which the Postgres datasource does silently, rather than
erroring.
"""

import sys
from pathlib import Path

import psycopg
import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "2.0" / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "2.0" / "scripts"))

from rule_evidence import DTE_BANDS, HELD_BANDS, SIZE_BANDS, TRR_TIERS  # noqa: E402

from common.db import connect  # noqa: E402

EXPECTED = {"trr": set(TRR_TIERS), "dte": set(DTE_BANDS),
            "size": set(SIZE_BANDS), "held": set(HELD_BANDS)}

# Which cohorts each dimension publishes. Dropping one silently narrows a panel:
# trr/all and size/all are what trading_trr_at_entry and
# trading_contract_buckets have always shown, and publishing only `earnings`
# would cut them by 48 and 88 positions.
EXPECTED_COHORTS = {
    ("trr", "earnings"), ("trr", "all"),
    ("dte", "earnings"),
    ("size", "earnings"), ("size", "all"),
    ("held", "SPREAD"), ("held", "SINGLE"),
}


def _snapshot():
    """Rows, or None when there is nothing to check here.

    Absent and empty are both "no data", and both are real states: CI has
    Postgres with an empty database, and before migration 007 reaches a host
    the table does not exist at all. Neither is an unreachable server, which
    this deliberately does NOT swallow.
    """
    with connect() as conn:
        try:
            return conn.execute(
                "SELECT dimension, cohort, band FROM rule_evidence_snapshot").fetchall()
        except psycopg.errors.UndefinedTable:
            conn.rollback()
            return None


@pytest.mark.pg
def test_snapshot_bands_match_rule_evidence():
    """Skips when the table is absent or empty, never when the server is
    unreachable -- those are different conditions, and CI (Postgres, no data)
    is exactly the case that separates them."""
    rows = _snapshot()
    if not rows:
        pytest.skip("rule_evidence_snapshot absent or empty")
    found: dict = {}
    for dimension, _, band in rows:
        found.setdefault(dimension, set()).add(band)
    assert found == EXPECTED, (
        f"snapshot bands drifted from rule_evidence.py: {found} != {EXPECTED}")


@pytest.mark.pg
def test_snapshot_publishes_every_cohort_the_panels_read():
    rows = _snapshot()
    if not rows:
        pytest.skip("rule_evidence_snapshot absent or empty")
    found = {(dimension, cohort) for dimension, cohort, _ in rows}
    assert found == EXPECTED_COHORTS, (
        f"published cohorts changed: {sorted(found)} != {sorted(EXPECTED_COHORTS)}")
