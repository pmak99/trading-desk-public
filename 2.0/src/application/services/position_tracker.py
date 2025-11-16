"""
Position tracking service for managing open and closed positions.

Provides functionality to:
- Add new positions
- Update position status and P&L
- Close positions
- Query open/closed positions
- Calculate portfolio metrics
"""

import sqlite3
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Dict, Any
from pathlib import Path
from decimal import Decimal

logger = logging.getLogger(__name__)

CONNECTION_TIMEOUT = 30  # seconds


@dataclass(frozen=True)
class Position:
    """Represents a trading position."""
    id: Optional[int]
    ticker: str
    entry_date: date
    earnings_date: date
    expiration_date: date

    # Strategy
    strategy_type: str
    num_contracts: int

    # Entry thesis
    credit_received: Decimal
    max_loss: Decimal
    vrp_ratio: Decimal
    implied_move_pct: Decimal
    historical_avg_move_pct: Decimal
    edge_score: Optional[Decimal] = None
    consistency_score: Optional[Decimal] = None
    skew_score: Optional[Decimal] = None

    # Position sizing
    position_size_pct: Decimal = Decimal("0")
    kelly_fraction: Optional[Decimal] = None

    # Risk parameters
    stop_loss_amount: Optional[Decimal] = None
    target_profit_amount: Optional[Decimal] = None
    breakeven_move_pct: Optional[Decimal] = None

    # Current status
    status: str = "OPEN"
    current_pnl: Decimal = Decimal("0")
    current_pnl_pct: Decimal = Decimal("0")
    days_held: int = 0

    # Close info
    close_date: Optional[date] = None
    close_price: Optional[Decimal] = None
    actual_move_pct: Optional[Decimal] = None
    final_pnl: Optional[Decimal] = None
    final_pnl_pct: Optional[Decimal] = None
    win_loss: Optional[str] = None

    # Metadata
    entry_notes: Optional[str] = None
    exit_notes: Optional[str] = None
    sector: Optional[str] = None
    market_cap_category: Optional[str] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class PortfolioSummary:
    """Summary of current portfolio state."""
    total_positions: int
    open_positions: int
    total_exposure_pct: Decimal
    total_capital_at_risk: Decimal
    unrealized_pnl: Decimal
    positions_at_stop_loss: List[str]
    positions_at_target: List[str]
    sector_exposure: Dict[str, Decimal]
    avg_vrp_ratio: Decimal
    avg_days_held: float


class PositionTracker:
    """Service for tracking trading positions."""

    def __init__(self, db_path: Path):
        """
        Initialize position tracker.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Ensure position tracking tables exist."""
        from src.infrastructure.database.positions_schema import add_positions_tables
        add_positions_tables(self.db_path)

    def add_position(self, position: Position) -> int:
        """
        Add a new position to tracking.

        Args:
            position: Position to add

        Returns:
            Position ID

        Raises:
            sqlite3.IntegrityError: If position already exists
        """
        conn = sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO positions (
                    ticker, entry_date, earnings_date, expiration_date,
                    strategy_type, num_contracts,
                    credit_received, max_loss, vrp_ratio,
                    implied_move_pct, historical_avg_move_pct,
                    edge_score, consistency_score, skew_score,
                    position_size_pct, kelly_fraction,
                    stop_loss_amount, target_profit_amount, breakeven_move_pct,
                    status, entry_notes, sector, market_cap_category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                position.ticker,
                position.entry_date.isoformat(),
                position.earnings_date.isoformat(),
                position.expiration_date.isoformat(),
                position.strategy_type,
                position.num_contracts,
                float(position.credit_received),
                float(position.max_loss),
                float(position.vrp_ratio),
                float(position.implied_move_pct),
                float(position.historical_avg_move_pct),
                float(position.edge_score) if position.edge_score else None,
                float(position.consistency_score) if position.consistency_score else None,
                float(position.skew_score) if position.skew_score else None,
                float(position.position_size_pct),
                float(position.kelly_fraction) if position.kelly_fraction else None,
                float(position.stop_loss_amount) if position.stop_loss_amount else None,
                float(position.target_profit_amount) if position.target_profit_amount else None,
                float(position.breakeven_move_pct) if position.breakeven_move_pct else None,
                position.status,
                position.entry_notes,
                position.sector,
                position.market_cap_category,
            ))

            position_id = cursor.lastrowid
            conn.commit()
            logger.info(f"✓ Position added: {position.ticker} (ID: {position_id})")
            return position_id

        except sqlite3.IntegrityError as e:
            logger.error(f"Position already exists: {position.ticker} on {position.entry_date}")
            raise
        finally:
            conn.close()

    def update_position_pnl(
        self,
        position_id: int,
        current_pnl: Decimal,
        current_price: Optional[Decimal] = None
    ) -> None:
        """
        Update position P&L and status.

        Args:
            position_id: Position ID
            current_pnl: Current profit/loss
            current_price: Current underlying price

        Raises:
            ValueError: If position not found
        """
        conn = sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT)
        conn.execute('BEGIN IMMEDIATE')  # Explicit transaction
        cursor = conn.cursor()

        try:
            # Get position details in same transaction
            cursor.execute('SELECT * FROM positions WHERE id = ?', (position_id,))
            row = cursor.fetchone()

            if not row:
                raise ValueError(f"Position {position_id} not found")

            position = self._row_to_position(row, cursor.description)

            # Calculate P&L percentage
            pnl_pct = (current_pnl / position.credit_received) * 100

            # Calculate days held
            days_held = (date.today() - position.entry_date).days

            cursor.execute('''
                UPDATE positions
                SET current_pnl = ?,
                    current_pnl_pct = ?,
                    days_held = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                float(current_pnl),
                float(pnl_pct),
                days_held,
                position_id
            ))

            conn.commit()
            logger.debug(f"Position {position_id} P&L updated: {current_pnl}")

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to update position {position_id}: {e}")
            raise
        finally:
            conn.close()

    def close_position(
        self,
        position_id: int,
        close_date: date,
        close_price: Decimal,
        actual_move_pct: Decimal,
        final_pnl: Decimal,
        exit_notes: Optional[str] = None
    ) -> None:
        """
        Close a position and record outcome.

        Args:
            position_id: Position ID
            close_date: Date position was closed
            close_price: Closing price of underlying
            actual_move_pct: Actual stock move percentage
            final_pnl: Final profit/loss
            exit_notes: Optional notes about exit
        """
        conn = sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT)
        cursor = conn.cursor()

        try:
            # Get position details
            position = self.get_position(position_id)
            if not position:
                raise ValueError(f"Position {position_id} not found")

            # Calculate final P&L percentage
            final_pnl_pct = (final_pnl / position.credit_received) * 100

            # Determine win/loss
            win_loss = "WIN" if final_pnl > 0 else "LOSS"

            # Calculate days held
            days_held = (close_date - position.entry_date).days

            cursor.execute('''
                UPDATE positions
                SET status = 'CLOSED',
                    close_date = ?,
                    close_price = ?,
                    actual_move_pct = ?,
                    final_pnl = ?,
                    final_pnl_pct = ?,
                    win_loss = ?,
                    days_held = ?,
                    exit_notes = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                close_date.isoformat(),
                float(close_price),
                float(actual_move_pct),
                float(final_pnl),
                float(final_pnl_pct),
                win_loss,
                days_held,
                exit_notes,
                position_id
            ))

            conn.commit()
            logger.info(
                f"✓ Position closed: {position.ticker} | "
                f"{win_loss} | P&L: ${final_pnl:.2f} ({final_pnl_pct:.1f}%)"
            )

        finally:
            conn.close()

    def get_position(self, position_id: int) -> Optional[Position]:
        """
        Get position by ID.

        Args:
            position_id: Position ID

        Returns:
            Position object or None if not found
        """
        conn = sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT)
        cursor = conn.cursor()

        try:
            cursor.execute('SELECT * FROM positions WHERE id = ?', (position_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return self._row_to_position(row, cursor.description)

        finally:
            conn.close()

    def get_open_positions(self) -> List[Position]:
        """
        Get all open positions.

        Returns:
            List of open positions
        """
        conn = sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT * FROM positions
                WHERE status = 'OPEN'
                ORDER BY entry_date DESC
            ''')

            positions = []
            for row in cursor.fetchall():
                positions.append(self._row_to_position(row, cursor.description))

            return positions

        finally:
            conn.close()

    def get_closed_positions(
        self,
        limit: int = 50,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[Position]:
        """
        Get closed positions.

        Args:
            limit: Maximum number of positions to return
            start_date: Filter by close date >= start_date
            end_date: Filter by close date <= end_date

        Returns:
            List of closed positions
        """
        conn = sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT)
        cursor = conn.cursor()

        try:
            query = 'SELECT * FROM positions WHERE status = "CLOSED"'
            params = []

            if start_date:
                query += ' AND close_date >= ?'
                params.append(start_date.isoformat())

            if end_date:
                query += ' AND close_date <= ?'
                params.append(end_date.isoformat())

            query += ' ORDER BY close_date DESC LIMIT ?'
            params.append(limit)

            cursor.execute(query, params)

            positions = []
            for row in cursor.fetchall():
                positions.append(self._row_to_position(row, cursor.description))

            return positions

        finally:
            conn.close()

    def get_portfolio_summary(self) -> PortfolioSummary:
        """
        Get current portfolio summary.

        Returns:
            PortfolioSummary object with current metrics
        """
        conn = sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT)
        cursor = conn.cursor()

        try:
            # Single aggregation query for performance
            cursor.execute('''
                SELECT
                    COUNT(*) as total_positions,
                    COALESCE(SUM(position_size_pct), 0) as total_exposure,
                    COALESCE(SUM(max_loss), 0) as capital_at_risk,
                    COALESCE(SUM(current_pnl), 0) as unrealized_pnl,
                    COALESCE(AVG(vrp_ratio), 0) as avg_vrp,
                    COALESCE(AVG(days_held), 0) as avg_days
                FROM positions
                WHERE status = 'OPEN'
            ''')

            summary_row = cursor.fetchone()
            total_positions = summary_row[0]
            total_exposure_pct = Decimal(str(summary_row[1]))
            total_capital_at_risk = Decimal(str(summary_row[2]))
            unrealized_pnl = Decimal(str(summary_row[3]))
            avg_vrp_ratio = Decimal(str(summary_row[4]))
            avg_days_held = float(summary_row[5])

            # Fetch individual positions for alerts and sector breakdown
            cursor.execute('''
                SELECT ticker, sector, position_size_pct,
                       current_pnl, stop_loss_amount, target_profit_amount
                FROM positions
                WHERE status = 'OPEN'
            ''')

            positions_at_stop_loss = []
            positions_at_target = []
            sector_exposure: Dict[str, Decimal] = {}

            for row in cursor.fetchall():
                ticker, sector, pos_size, curr_pnl, stop_loss, target = row

                # Check stop loss
                if stop_loss and curr_pnl <= -stop_loss:
                    positions_at_stop_loss.append(ticker)

                # Check target
                if target and curr_pnl >= target:
                    positions_at_target.append(ticker)

                # Sector exposure
                if sector:
                    sector_exposure[sector] = sector_exposure.get(sector, Decimal("0")) + Decimal(str(pos_size))
        finally:
            conn.close()

        return PortfolioSummary(
            total_positions=total_positions,
            open_positions=total_positions,
            total_exposure_pct=total_exposure_pct,
            total_capital_at_risk=total_capital_at_risk,
            unrealized_pnl=unrealized_pnl,
            positions_at_stop_loss=positions_at_stop_loss,
            positions_at_target=positions_at_target,
            sector_exposure=sector_exposure,
            avg_vrp_ratio=avg_vrp_ratio,
            avg_days_held=avg_days_held
        )

    def _row_to_position(self, row: tuple, description: list) -> Position:
        """Convert database row to Position object."""
        # Create dict from row
        columns = [col[0] for col in description]
        data = dict(zip(columns, row))

        # Convert dates
        entry_date = date.fromisoformat(data['entry_date'])
        earnings_date = date.fromisoformat(data['earnings_date'])
        expiration_date = date.fromisoformat(data['expiration_date'])
        close_date = date.fromisoformat(data['close_date']) if data.get('close_date') else None

        return Position(
            id=data['id'],
            ticker=data['ticker'],
            entry_date=entry_date,
            earnings_date=earnings_date,
            expiration_date=expiration_date,
            strategy_type=data['strategy_type'],
            num_contracts=data['num_contracts'],
            credit_received=Decimal(str(data['credit_received'])),
            max_loss=Decimal(str(data['max_loss'])),
            vrp_ratio=Decimal(str(data['vrp_ratio'])),
            implied_move_pct=Decimal(str(data['implied_move_pct'])),
            historical_avg_move_pct=Decimal(str(data['historical_avg_move_pct'])),
            edge_score=Decimal(str(data['edge_score'])) if data.get('edge_score') else None,
            consistency_score=Decimal(str(data['consistency_score'])) if data.get('consistency_score') else None,
            skew_score=Decimal(str(data['skew_score'])) if data.get('skew_score') else None,
            position_size_pct=Decimal(str(data['position_size_pct'])),
            kelly_fraction=Decimal(str(data['kelly_fraction'])) if data.get('kelly_fraction') else None,
            stop_loss_amount=Decimal(str(data['stop_loss_amount'])) if data.get('stop_loss_amount') else None,
            target_profit_amount=Decimal(str(data['target_profit_amount'])) if data.get('target_profit_amount') else None,
            breakeven_move_pct=Decimal(str(data['breakeven_move_pct'])) if data.get('breakeven_move_pct') else None,
            status=data['status'],
            current_pnl=Decimal(str(data.get('current_pnl', 0))),
            current_pnl_pct=Decimal(str(data.get('current_pnl_pct', 0))),
            days_held=data.get('days_held', 0),
            close_date=close_date,
            close_price=Decimal(str(data['close_price'])) if data.get('close_price') else None,
            actual_move_pct=Decimal(str(data['actual_move_pct'])) if data.get('actual_move_pct') else None,
            final_pnl=Decimal(str(data['final_pnl'])) if data.get('final_pnl') else None,
            final_pnl_pct=Decimal(str(data['final_pnl_pct'])) if data.get('final_pnl_pct') else None,
            win_loss=data.get('win_loss'),
            entry_notes=data.get('entry_notes'),
            exit_notes=data.get('exit_notes'),
            sector=data.get('sector'),
            market_cap_category=data.get('market_cap_category'),
        )
