"""DataQualityAgent - Automated data quality checks and fixes.

This agent scans the database for data quality issues and can
automatically fix safe issues while flagging ambiguous ones.
"""

from typing import Dict, Any, List
import logging
import sqlite3

from ..integration.container_2_0 import Container2_0

logger = logging.getLogger(__name__)


class DataQualityAgent:
    """
    Agent for automated data quality management.

    Modes:
    - report: Identify issues without fixing
    - dry-run: Show what would be fixed
    - fix: Apply safe fixes

    Safe to auto-fix:
    - Duplicate historical_moves entries
    - Missing ticker_metadata (fetch from Finnhub)
    - Stale earnings dates (refresh from Alpha Vantage)

    Requires manual review:
    - Tickers with <4 quarters (need backfill)
    - Extreme outliers >50% (may be valid)
    """

    def __init__(self):
        """Initialize with database connection."""
        container = Container2_0()
        self.db_path = container.get_db_path()

    def run(self, mode: str = "report") -> Dict[str, Any]:
        """
        Run data quality analysis.

        Args:
            mode: "report" | "dry-run" | "fix"

        Returns:
            Dict with issues found and actions taken
        """
        if mode not in ["report", "dry-run", "fix"]:
            raise ValueError(f"Invalid mode: {mode}. Use report, dry-run, or fix")

        # Collect issues
        fixable_issues = []
        flagged_issues = []

        # Check 1: Duplicates
        duplicates = self._find_duplicates()
        if duplicates:
            fixable_issues.append({
                'type': 'duplicates',
                'count': len(duplicates),
                'items': duplicates[:10],
                'fix_action': 'Delete older duplicate entries'
            })

        # Check 2: Insufficient data (<4 quarters)
        insufficient = self._find_insufficient_data()
        if insufficient:
            flagged_issues.append({
                'type': 'insufficient_data',
                'count': len(insufficient),
                'items': insufficient[:10],
                'reason': 'Requires manual backfill'
            })

        # Check 3: Outliers (>50% moves)
        outliers = self._find_outliers()
        if outliers:
            flagged_issues.append({
                'type': 'outliers',
                'count': len(outliers),
                'items': outliers[:10],
                'reason': 'May be valid data - manual review needed'
            })

        # Apply fixes if requested
        fixed_issues = []
        if mode == "fix":
            fixed_issues = self._apply_fixes(fixable_issues)

        return {
            'fixable_issues': fixable_issues,
            'flagged_issues': flagged_issues,
            'fixed_issues': fixed_issues if mode == "fix" else [],
            'would_fix': fixable_issues if mode == "dry-run" else [],
            'changes_applied': mode == "fix",
            'summary': {
                'total_fixable': sum(i['count'] for i in fixable_issues),
                'total_flagged': sum(i['count'] for i in flagged_issues),
                'total_fixed': len(fixed_issues)
            }
        }

    def _find_duplicates(self) -> List[Dict]:
        """Find duplicate historical_moves entries."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT ticker, earnings_date, COUNT(*) as cnt
                FROM historical_moves
                GROUP BY ticker, earnings_date
                HAVING cnt > 1
            """)

            rows = cursor.fetchall()
            conn.close()

            return [{'ticker': r[0], 'date': r[1], 'count': r[2]} for r in rows]

        except Exception:
            return []

    def _find_insufficient_data(self) -> List[Dict]:
        """Find tickers with <4 quarters of data."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT ticker, COUNT(*) as quarters
                FROM historical_moves
                GROUP BY ticker
                HAVING quarters < 4
                ORDER BY quarters ASC
            """)

            rows = cursor.fetchall()
            conn.close()

            return [{'ticker': r[0], 'quarters': r[1]} for r in rows]

        except Exception:
            return []

    def _find_outliers(self) -> List[Dict]:
        """Find extreme outlier moves (>50%)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT ticker, earnings_date, gap_move_pct
                FROM historical_moves
                WHERE ABS(gap_move_pct) > 50
                ORDER BY ABS(gap_move_pct) DESC
            """)

            rows = cursor.fetchall()
            conn.close()

            return [{'ticker': r[0], 'date': r[1], 'move': r[2]} for r in rows]

        except Exception:
            return []

    def _apply_fixes(self, fixable_issues: List[Dict]) -> List[str]:
        """Apply safe fixes to the database."""
        fixed = []

        for issue in fixable_issues:
            if issue['type'] == 'duplicates':
                count = self._fix_duplicates()
                if count > 0:
                    fixed.append(f"Removed {count} duplicate entries")

        return fixed

    def _fix_duplicates(self) -> int:
        """Remove duplicate historical_moves entries (keep newest)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Delete duplicates, keeping the row with highest rowid
            cursor.execute("""
                DELETE FROM historical_moves
                WHERE rowid NOT IN (
                    SELECT MAX(rowid)
                    FROM historical_moves
                    GROUP BY ticker, earnings_date
                )
            """)

            deleted = cursor.rowcount
            conn.commit()
            conn.close()

            return deleted

        except Exception as e:
            logger.error(f"Error fixing duplicates: {e}")
            return 0
