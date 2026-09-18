"""analysis_log provenance: which command produced each row.

analyze_ticker is shared by whisper_mode, scan_mode and ticker_mode, so the
value threads from four call sites, not two.
"""

import inspect
import re
from pathlib import Path

from src.infrastructure.database.repositories.analysis_repository import (
    AnalysisRepository,
)

REPO_SRC = (
    Path(__file__).parent.parent.parent
    / "src/infrastructure/database/repositories/analysis_repository.py"
)


def test_log_analysis_accepts_source_kwarg():
    sig = inspect.signature(AnalysisRepository.log_analysis)
    assert "source" in sig.parameters, "log_analysis must accept a source kwarg"
    assert sig.parameters["source"].default is None, "source must default to None"


def test_insert_names_the_source_column():
    sql = REPO_SRC.read_text(encoding="utf-8")
    insert = re.search(
        r"INSERT INTO analysis_log \((.*?)\)\s*VALUES", sql, re.S
    )
    assert insert, "could not locate the analysis_log INSERT"
    cols = [
        c.strip()
        for c in re.sub(r"--[^\n]*", "", insert.group(1)).split(",")
        if c.strip()
    ]
    assert "source" in cols, f"source missing from INSERT columns: {cols}"


def test_insert_column_and_placeholder_counts_match():
    """Adding a column without adding its '?' is the classic failure here —
    it raises only at runtime, inside a fire-and-forget try/except."""
    sql = REPO_SRC.read_text(encoding="utf-8")
    m = re.search(
        r"INSERT INTO analysis_log \((.*?)\)\s*VALUES\s*\((.*?)\)", sql, re.S
    )
    assert m, "could not locate the analysis_log INSERT ... VALUES"
    cols = [
        c.strip()
        for c in re.sub(r"--[^\n]*", "", m.group(1)).split(",")
        if c.strip()
    ]
    placeholders = [p.strip() for p in m.group(2).split(",") if p.strip()]
    assert len(cols) == len(placeholders), (
        f"{len(cols)} columns but {len(placeholders)} placeholders"
    )


def test_analyze_ticker_accepts_source_kwarg():
    from scripts.scan.workflows.ticker_analysis import analyze_ticker

    sig = inspect.signature(analyze_ticker)
    assert "source" in sig.parameters
    assert sig.parameters["source"].default == "scan"


def test_analyze_ticker_concurrent_accepts_source_kwarg():
    from scripts.scan.workflows.ticker_analysis import analyze_ticker_concurrent

    sig = inspect.signature(analyze_ticker_concurrent)
    assert "source" in sig.parameters


# Removed at the Postgres cutover: test_sync_column_list_includes_source.
# It asserted that sync_analysis_log's ANALYSIS_LOG_COLUMNS carried `source`,
# so provenance would not be dropped when merging the Mac's rows into
# the server's. There is one database now -- no merge, no column list, and
# sync_databases.py is gone. The guarantee it protected is structural rather
# than tested: a column cannot be lost in transit when nothing is in transit.
