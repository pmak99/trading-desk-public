"""Characterization tests for scan.workflows.vix — extracted from workflows.py."""
import sys
from pathlib import Path
from unittest.mock import patch

# Import vix directly via sys.path injection so the test remains isolated.
# workflows/ is a package, but this avoids triggering the full package init
# for a narrow characterization test of just the vix module.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts" / "scan" / "workflows"))

from vix import _check_vix_term_structure


def test_check_vix_term_structure_returns_unknown_on_fetch_error():
    """Error path: any exception → (None, None, 'UNKNOWN')."""
    with patch("yfinance.download", side_effect=ConnectionError("network error")):
        result = _check_vix_term_structure()
    assert result == (None, None, "UNKNOWN")


def test_check_vix_term_structure_returns_unknown_on_bad_data():
    """Bad data path: IndexError in .iloc[-1] → (None, None, 'UNKNOWN')."""
    import pandas as pd
    from unittest.mock import MagicMock

    bad_df = MagicMock()
    bad_df.__getitem__.return_value.iloc.__getitem__.side_effect = IndexError("empty")

    with patch("yfinance.download", return_value=bad_df):
        result = _check_vix_term_structure()
    assert result == (None, None, "UNKNOWN")


def _df_with(vix, vix3m):
    import pandas as pd
    return pd.DataFrame(
        {("Close", "^VIX"): [vix], ("Close", "^VIX3M"): [vix3m]}
    )


def test_nan_vix3m_is_unknown_not_stress():
    """A failed ^VIX3M leg comes back NaN, not an exception. NaN falls
    through every ratio comparison to the STRESS branch — the most alarming
    label for a missing-data condition. Must report UNKNOWN instead."""
    with patch("yfinance.download", return_value=_df_with(15.8, float("nan"))):
        vix, vix3m, label = _check_vix_term_structure()
    assert label == "UNKNOWN"
    assert vix3m is None


def test_nan_vix_is_unknown(monkeypatch):
    with patch("yfinance.download", return_value=_df_with(float("nan"), 18.0)):
        _, _, label = _check_vix_term_structure()
    assert label == "UNKNOWN"


def test_real_stress_still_labeled():
    # ratio 0.80 < 0.85 -> genuine STRESS
    with patch("yfinance.download", return_value=_df_with(40.0, 32.0)):
        vix, vix3m, label = _check_vix_term_structure()
    assert label == "STRESS"
    assert vix == 40.0 and vix3m == 32.0
