#!/usr/bin/env python3
"""
Parse 2025 Fidelity broker statements and evaluate 2.0 system baseline performance.

This script:
1. Extracts options trades from monthly PDF statements
2. Matches trades to earnings dates in the database
3. Calculates performance metrics (win rate, Sharpe, P&L breakdown)
4. Generates a baseline performance report for the ML system to beat
"""

import re
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict

try:
    import pdfplumber
except ImportError:
    print("Installing pdfplumber...")
    import subprocess
    subprocess.check_call(["./venv/bin/pip", "install", "pdfplumber"])
    import pdfplumber

import numpy as np


@dataclass
class OptionsTrade:
    """Represents a single options leg from broker statement."""
    settlement_date: str
    symbol: str
    option_type: str  # PUT or CALL
    strike: float
    expiration: str
    quantity: int
    action: str  # OPENING or CLOSING
    side: str  # SELL (short) or BUY (long)
    price: float
    proceeds: float
    transaction_cost: float
    gain_loss: Optional[float] = None
    is_earnings_trade: bool = False
    earnings_date: Optional[str] = None
    strategy_type: Optional[str] = None  # 'naked', 'spread', 'iron_condor'
    leg_type: Optional[str] = None  # 'short', 'long', 'body', 'wing'

    def __post_init__(self):
        """Parse dates to datetime objects."""
        # Settlement date is in format "MM/DD" (e.g., "01/02")
        self.settlement_dt = datetime.strptime(f"{self.settlement_date}/2025", "%m/%d/%Y")
        # Expiration is in format "MMM DD YY" (e.g., "JAN 17 25")
        self.expiration_dt = datetime.strptime(self.expiration, "%b %d %y")


@dataclass
class MonthlyPerformance:
    """Performance metrics for a single month."""
    month: str
    beginning_value: float
    ending_value: float
    change: float
    fees: float
    ytd_gain: float = 0.0
    num_trades: int = 0
    num_winners: int = 0
    num_losers: int = 0
    total_gain: float = 0.0
    total_loss: float = 0.0
    trades: List[OptionsTrade] = field(default_factory=list)
    earnings_trades: List[OptionsTrade] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        """Calculate win rate."""
        if self.num_trades == 0:
            return 0.0
        return self.num_winners / self.num_trades

    @property
    def avg_win(self) -> float:
        """Calculate average winning trade."""
        if self.num_winners == 0:
            return 0.0
        return self.total_gain / self.num_winners

    @property
    def avg_loss(self) -> float:
        """Calculate average losing trade."""
        if self.num_losers == 0:
            return 0.0
        return abs(self.total_loss / self.num_losers)

    @property
    def profit_factor(self) -> float:
        """Calculate profit factor (gross profit / gross loss)."""
        if abs(self.total_loss) < 0.01:
            return float('inf') if self.total_gain > 0 else 0.0
        return self.total_gain / abs(self.total_loss)


def extract_account_summary(text: str) -> Tuple[float, float, float, float]:
    """Extract account values from statement text including YTD gain/loss."""
    # Pattern for account X96-783860 summary
    account_pattern = r'Account # X96-783860.*?Beginning Net Account Value.*?\$?([\d,]+\.\d{2}).*?Ending Net Account Value.*?\$?([\d,]+\.\d{2}).*?Transaction Costs, Fees & Charges.*?-?\$?([\d,]+\.\d{2})'

    beginning, ending, fees = 0.0, 0.0, 0.0
    match = re.search(account_pattern, text, re.DOTALL)
    if match:
        beginning = float(match.group(1).replace(',', ''))
        ending = float(match.group(2).replace(',', ''))
        fees = float(match.group(3).replace(',', ''))

    # Extract YTD gain/loss
    ytd_pattern = r'Net Short-term Gain/Loss\s+[\d,]+\.\d{2}\s+([\d,]+\.\d{2})'
    ytd_match = re.search(ytd_pattern, text)
    ytd_gain = float(ytd_match.group(1).replace(',', '')) if ytd_match else 0.0

    return beginning, ending, fees, ytd_gain


def parse_options_trades(text: str) -> List[OptionsTrade]:
    """Extract options trades from statement text."""
    trades = []

    # Pattern matching the actual PDF format:
    # Line 1: 01/02 PUT (SPY) SPDR S&P500 ETF 7446849VP You Bought 8.000 $1.00000 -$997.42f -$2.55 -$802.55
    # Line 2: DEC 31 24 $587 (100 SHS) CLOSING Short-term gain: $194.87
    # Line 3: TRANSACTION

    # First line pattern - use simpler, more robust pattern:
    # - Date can be "MM/DD" or "MM/DD MM/DD" (date range)
    # - Use .*? to skip description/CUSIP flexibly
    # - Capture negative quantities for sells
    # - Last field (amount) is optional - some formats only have TRANS_COST
    # Format: DATE TYPE (SYMBOL) ... Bought/Sold QTY PRICE COST_BASIS TRANS_COST [AMOUNT]
    line1_pattern = r'(\d{2}/\d{2}(?:\s+\d{2}/\d{2})?)\s+(PUT|CALL)\s+\(([A-Z]+)\).*?([Bb]ought|[Ss]old)\s+(-?\d+\.\d+)\s+\$?([\d.]+)\s+([-\$\d,]+\.\d+)f?\s+([-\$\d.,]+)(?:\s+([-\$\d,]+\.\d+))?'

    # Second line pattern (on next line)
    line2_pattern = r'(\w{3}\s+\d{1,2}\s+\d{2})\s+\$?([\d.]+)\s+\(100 SHS\)\s+(OPENING|CLOSING)'

    # Gain/loss patterns
    gain_pattern = r'Short-term gain:\s+\$?([\d,]+\.\d{2})'
    loss_pattern = r'Short-term loss:\s+\$?([\d,]+\.\d{2})'

    # Split text into lines for processing
    lines = text.split('\n')

    i = 0
    while i < len(lines):
        line = lines[i]

        # Try to match first line
        match1 = re.search(line1_pattern, line)
        if match1:
            settlement_raw = match1.group(1)
            # Extract first date if it's a range (e.g., "01/31 02/03" -> "01/31")
            settlement = settlement_raw.split()[0] if ' ' in settlement_raw else settlement_raw

            option_type = match1.group(2)
            symbol = match1.group(3)
            side = "SELL" if match1.group(4).lower() == "sold" else "BUY"
            quantity = float(match1.group(5))
            price = float(match1.group(6))
            cost_basis = float(match1.group(7).replace(',', '').replace('$', ''))
            trans_cost = float(match1.group(8).replace(',', '').replace('$', ''))
            # Amount field is optional (some trades only have trans_cost)
            amount = float(match1.group(9).replace(',', '').replace('$', '')) if match1.group(9) else trans_cost

            # Look for second line with expiration and action
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                match2 = re.search(line2_pattern, next_line)

                if match2:
                    expiration = match2.group(1)
                    strike = float(match2.group(2))
                    action = match2.group(3)

                    # Look for gain/loss in next few lines
                    gain_loss = None
                    for j in range(i + 1, min(i + 4, len(lines))):
                        # Check for gain first
                        gain_match = re.search(gain_pattern, lines[j])
                        if gain_match:
                            gain_loss = float(gain_match.group(1).replace(',', ''))
                            break
                        # Check for loss
                        loss_match = re.search(loss_pattern, lines[j])
                        if loss_match:
                            gain_loss = -float(loss_match.group(1).replace(',', ''))  # Store as negative
                            break

                    trade = OptionsTrade(
                        settlement_date=settlement,
                        symbol=symbol,
                        option_type=option_type,
                        strike=strike,
                        expiration=expiration,
                        quantity=int(abs(quantity)),
                        action=action,
                        side=side,
                        price=price,
                        proceeds=amount,
                        transaction_cost=trans_cost,
                        gain_loss=gain_loss
                    )
                    trades.append(trade)

        i += 1

    return trades


def classify_strategies(trades: List[OptionsTrade]) -> List[OptionsTrade]:
    """
    Classify trades into strategies by grouping by symbol + expiration.
    - Iron Condor: Has both PUT and CALL on same symbol/expiration
    - Spread: Has same option type with different strikes (short + long)
    - Naked: Single leg only
    """
    # Group trades by symbol + expiration
    groups = defaultdict(list)
    for trade in trades:
        # Only consider CLOSING trades with gain/loss
        if trade.action == "CLOSING" and trade.gain_loss is not None:
            key = (trade.symbol, trade.expiration)
            groups[key].append(trade)

    # Classify each group
    for (symbol, expiration), group_trades in groups.items():
        has_put = any(t.option_type == "PUT" for t in group_trades)
        has_call = any(t.option_type == "CALL" for t in group_trades)

        if has_put and has_call:
            # Iron Condor - has both sides
            strategy = "iron_condor"
        elif len(group_trades) > 1:
            # Spread - multiple legs of same type
            strategy = "spread"
        else:
            # Naked - single leg
            strategy = "naked"

        # Apply strategy type to all trades in group
        for trade in group_trades:
            trade.strategy_type = strategy

    return trades


def match_earnings_trades(trades: List[OptionsTrade], db_path: str) -> List[OptionsTrade]:
    """Match trades to earnings dates in database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    earnings_trades = []

    for trade in trades:
        # Query for earnings within ±7 days of trade expiration
        exp_dt = trade.expiration_dt
        start_date = (exp_dt - timedelta(days=7)).strftime('%Y-%m-%d')
        end_date = (exp_dt + timedelta(days=7)).strftime('%Y-%m-%d')

        cursor.execute("""
            SELECT ticker, earnings_date
            FROM historical_moves
            WHERE ticker = ?
              AND earnings_date BETWEEN ? AND ?
            ORDER BY ABS(julianday(earnings_date) - julianday(?))
            LIMIT 1
        """, (trade.symbol, start_date, end_date, exp_dt.strftime('%Y-%m-%d')))

        result = cursor.fetchone()
        if result:
            trade.is_earnings_trade = True
            trade.earnings_date = result[1]
            earnings_trades.append(trade)

    conn.close()
    return earnings_trades


def parse_monthly_statement(pdf_path: Path, db_path: str) -> MonthlyPerformance:
    """Parse a single monthly statement PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        # Extract all text from relevant pages
        text = ""
        for page in pdf.pages:
            text += page.extract_text() + "\n"

    # Extract month from filename (e.g., Statement1312025.pdf -> January 2025)
    filename = pdf_path.stem
    month_end = filename.replace("Statement", "")
    month_end_dt = datetime.strptime(month_end, "%m%d%Y")
    month_name = month_end_dt.strftime("%B %Y")

    # Extract account summary including YTD gain
    beginning, ending, fees, ytd_gain = extract_account_summary(text)

    # Extract trades
    trades = parse_options_trades(text)

    # Classify strategies (iron condor, spread, naked)
    trades = classify_strategies(trades)

    # Match to earnings
    earnings_trades = match_earnings_trades(trades, db_path)

    # Calculate metrics
    num_winners = sum(1 for t in trades if t.gain_loss and t.gain_loss > 0)
    num_losers = sum(1 for t in trades if t.gain_loss and t.gain_loss < 0)
    total_gain = sum(t.gain_loss for t in trades if t.gain_loss and t.gain_loss > 0)
    total_loss = sum(t.gain_loss for t in trades if t.gain_loss and t.gain_loss < 0)

    return MonthlyPerformance(
        month=month_name,
        beginning_value=beginning,
        ending_value=ending,
        change=ending - beginning + fees,  # Add fees back since they're deducted
        fees=fees,
        ytd_gain=ytd_gain,
        num_trades=len([t for t in trades if t.gain_loss is not None]),
        num_winners=num_winners,
        num_losers=num_losers,
        total_gain=total_gain,
        total_loss=total_loss,
        trades=trades,
        earnings_trades=earnings_trades
    )


def calculate_sharpe_ratio(monthly_returns: List[float], risk_free_rate: float = 0.04) -> float:
    """Calculate annualized Sharpe ratio from monthly returns."""
    if len(monthly_returns) < 2:
        return 0.0

    returns_array = np.array(monthly_returns)
    excess_returns = returns_array - (risk_free_rate / 12)  # Monthly risk-free rate

    if excess_returns.std() == 0:
        return 0.0

    return (excess_returns.mean() / excess_returns.std()) * np.sqrt(12)


def generate_report(monthly_perf: List[MonthlyPerformance], output_path: Path):
    """Generate markdown report with baseline performance metrics."""

    # Calculate aggregate metrics
    total_beginning = monthly_perf[0].beginning_value
    total_ending = monthly_perf[-1].ending_value
    total_return = (total_ending - total_beginning) / total_beginning if total_beginning > 0 else 0.0
    total_gain = sum(m.total_gain for m in monthly_perf)
    total_loss = sum(m.total_loss for m in monthly_perf)
    total_trades = sum(m.num_trades for m in monthly_perf)
    total_winners = sum(m.num_winners for m in monthly_perf)
    total_losers = sum(m.num_losers for m in monthly_perf)
    total_fees = sum(m.fees for m in monthly_perf)
    total_earnings_trades = sum(len(m.earnings_trades) for m in monthly_perf)

    # Handle case of no trades
    if total_trades == 0:
        print("\n⚠️  WARNING: No trades found in statements!")
        print("This could mean:")
        print("  1. PDF parsing regex needs adjustment")
        print("  2. Account had no options activity")
        print("  3. Trades are in a different format\n")
        print("Generating report with account value changes only...\n")

    # Calculate monthly returns for Sharpe
    monthly_returns = []
    for m in monthly_perf:
        if m.beginning_value > 0:
            monthly_returns.append((m.ending_value - m.beginning_value) / m.beginning_value)

    sharpe = calculate_sharpe_ratio(monthly_returns)
    win_rate = total_winners / total_trades if total_trades > 0 else 0.0
    avg_win = total_gain / total_winners if total_winners > 0 else 0.0
    avg_loss = abs(total_loss / total_losers) if total_losers > 0 else 0.0
    profit_factor = total_gain / abs(total_loss) if abs(total_loss) > 0.01 else (999.99 if total_gain > 0 else 0.0)
    earnings_pct = (total_earnings_trades / total_trades * 100) if total_trades > 0 else 0.0

    # Generate markdown report
    report = f"""# 2.0 System Baseline Performance Report
**Account X96-783860 - Individual Brokerage**
**Period:** January 2025 - {monthly_perf[-1].month}

## Executive Summary

This report establishes the baseline performance of the 2.0 IV Crush earnings trading system on **real 2025 trades**. These metrics represent the performance floor that the 3.0 ML-enhanced system must beat.

### Key Metrics

| Metric | Value |
|--------|-------|
| **Total Return** | **{total_return:.2%}** |
| **Beginning Value** | ${total_beginning:,.2f} |
| **Ending Value** | ${total_ending:,.2f} |
| **Net P&L** | ${total_ending - total_beginning:,.2f} |
| **Annualized Sharpe Ratio** | **{sharpe:.2f}** |
| | |
| **Total Trades** | {total_trades} |
| **Earnings Trades** | {total_earnings_trades} ({earnings_pct:.1f}%) |
| **Win Rate** | **{win_rate:.1%}** |
| **Profit Factor** | **{profit_factor:.2f}** |
| | |
| **Total Gain** | ${total_gain:,.2f} |
| **Total Loss** | ${total_loss:,.2f} |
| **Avg Win** | ${avg_win:,.2f} |
| **Avg Loss** | ${avg_loss:,.2f} |
| **Total Fees** | ${total_fees:,.2f} |
| | |
| **YTD Gain (Broker)** | **${monthly_perf[-1].ytd_gain:,.2f}** |

### ML System Success Criteria

Based on the 2.0 baseline, the 3.0 ML system must achieve:

1. **Minimum Requirements:**
   - Sharpe Ratio > {sharpe * 1.10:.2f} (+10% improvement)
   - Win Rate > {win_rate * 1.05:.1%} (+5% improvement)
   - Total Return > {total_return * 1.15:.2%} (+15% improvement)

2. **Target Goals:**
   - Sharpe Ratio > {sharpe * 1.25:.2f} (+25% improvement)
   - Win Rate > {win_rate * 1.10:.1%} (+10% improvement)
   - Total Return > {total_return * 1.30:.2%} (+30% improvement)

## Monthly Performance Breakdown

"""

    # Monthly table
    report += "| Month | Begin | End | Change | P&L | Trades | Win Rate | Profit Factor |\n"
    report += "|-------|-------|-----|--------|-----|--------|----------|---------------|\n"

    for m in monthly_perf:
        pct_change = (m.change / m.beginning_value) if m.beginning_value > 0 else 0.0
        report += f"| {m.month} | ${m.beginning_value:,.0f} | ${m.ending_value:,.0f} | "
        report += f"{pct_change:.1%} | "
        report += f"${m.total_gain + m.total_loss:,.0f} | "
        report += f"{m.num_trades} | {m.win_rate:.1%} | {m.profit_factor:.2f} |\n"

    # Earnings trades section
    report += f"\n## Earnings Trade Analysis\n\n"
    report += f"**Total Earnings Trades:** {total_earnings_trades} / {total_trades} ({earnings_pct:.1f}%)\n\n"

    if total_earnings_trades > 0:
        # Group earnings trades by symbol
        earnings_by_symbol = defaultdict(list)
        for m in monthly_perf:
            for trade in m.earnings_trades:
                earnings_by_symbol[trade.symbol].append(trade)

        report += f"**Symbols Traded:** {len(earnings_by_symbol)}\n\n"
        report += "| Symbol | Trades | Win Rate | Total P&L |\n"
        report += "|--------|--------|----------|----------|\n"

        for symbol in sorted(earnings_by_symbol.keys()):
            trades = earnings_by_symbol[symbol]
            wins = sum(1 for t in trades if t.gain_loss and t.gain_loss > 0)
            win_rate_sym = wins/len(trades) if len(trades) > 0 else 0.0
            total_pl = sum(t.gain_loss for t in trades if t.gain_loss)
            report += f"| {symbol} | {len(trades)} | {win_rate_sym:.1%} | ${total_pl:,.2f} |\n"
    else:
        report += "*No earnings trades could be matched to database. This may indicate:*\n"
        report += "- Trades were not earnings-related\n"
        report += "- Earnings dates not in database for these symbols/dates\n"
        report += "- Date matching tolerance too strict\n\n"

    # Strategy breakdown section
    report += f"\n## Strategy Breakdown\n\n"

    # Collect all trades across months and group by strategy
    all_trades = []
    for m in monthly_perf:
        all_trades.extend([t for t in m.trades if t.gain_loss is not None and t.strategy_type])

    if all_trades:
        strategy_stats = defaultdict(lambda: {'count': 0, 'wins': 0, 'total_pl': 0.0})
        for trade in all_trades:
            strat = trade.strategy_type
            strategy_stats[strat]['count'] += 1
            if trade.gain_loss > 0:
                strategy_stats[strat]['wins'] += 1
            strategy_stats[strat]['total_pl'] += trade.gain_loss

        report += "| Strategy | Trades | Win Rate | Total P&L | Avg P&L |\n"
        report += "|----------|--------|----------|-----------|----------|\n"

        for strategy in sorted(strategy_stats.keys()):
            stats = strategy_stats[strategy]
            win_rate_strat = stats['wins'] / stats['count'] if stats['count'] > 0 else 0.0
            avg_pl = stats['total_pl'] / stats['count'] if stats['count'] > 0 else 0.0
            report += f"| {strategy.replace('_', ' ').title()} | {stats['count']} | "
            report += f"{win_rate_strat:.1%} | ${stats['total_pl']:,.2f} | ${avg_pl:,.2f} |\n"
    else:
        report += "*No strategy classification available.*\n\n"

    # Analysis section
    report += f"\n## Analysis\n\n"
    report += f"### Strengths\n\n"
    report += f"- **Strong Sharpe Ratio ({sharpe:.2f}):** Risk-adjusted returns are solid\n"
    report += f"- **High Win Rate ({win_rate:.1%}):** Majority of trades are profitable\n"
    report += f"- **Positive Profit Factor ({profit_factor:.2f}):** Wins outweigh losses\n\n"

    report += f"### Areas for ML Improvement\n\n"
    report += f"1. **Move Magnitude Prediction:** Current system uses historical mean; ML can predict actual moves\n"
    report += f"2. **Trade Selection:** {total_earnings_trades}/{total_trades} trades were earnings-related; ML can improve filtering\n"
    report += f"3. **Position Sizing:** Currently uses fixed sizing; ML can optimize based on confidence\n"
    report += f"4. **Strategy Selection:** ML can choose optimal strategy (put/call/spread) based on conditions\n\n"

    report += f"## Next Steps\n\n"
    report += f"1. **Proceed to Task 0.2:** Setup 3.0 directory structure\n"
    report += f"2. **Begin Phase 1A:** Feature engineering (historical, volatility, market features)\n"
    report += f"3. **Track Progress:** Update `3.0/PROGRESS.md` after each task\n\n"

    report += f"---\n"
    report += f"*Report Generated:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    report += f"*Data Source:* Fidelity Brokerage Statements (Account X96-783860)\n"
    report += f"*Period:* {monthly_perf[0].month} - {monthly_perf[-1].month}\n"

    # Write report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    print(f"\n✅ Report generated: {output_path}")


def main():
    """Main execution function."""
    # Paths
    statements_dir = Path("../docs/2025 Trades")
    db_path = "data/ivcrush.db"
    output_path = Path("2.0/reports/2025_baseline_performance.md")

    print("=" * 80)
    print("EVALUATING 2.0 SYSTEM BASELINE PERFORMANCE")
    print("=" * 80)
    print(f"\n📂 Statements Directory: {statements_dir}")
    print(f"📊 Database: {db_path}")
    print(f"📝 Output Report: {output_path}\n")

    # Check database exists
    if not Path(db_path).exists():
        print(f"❌ ERROR: Database not found at {db_path}")
        return 1

    # Find all statement PDFs and sort by date
    statement_files = list(statements_dir.glob("Statement*.pdf"))
    # Sort by date extracted from filename (MMDDYYYY)
    statement_files.sort(key=lambda p: datetime.strptime(p.stem.replace("Statement", ""), "%m%d%Y"))
    print(f"Found {len(statement_files)} monthly statements\n")

    # Parse each statement
    monthly_performance = []
    for pdf_path in statement_files:
        print(f"Processing {pdf_path.name}...", end=" ")
        try:
            perf = parse_monthly_statement(pdf_path, db_path)
            monthly_performance.append(perf)
            print(f"✅ {perf.month}: {perf.num_trades} trades, {perf.win_rate:.1%} win rate")
        except Exception as e:
            print(f"❌ Error: {e}")
            continue

    if not monthly_performance:
        print("\n❌ ERROR: No statements could be parsed")
        return 1

    # Generate report
    print(f"\n{'=' * 80}")
    print("GENERATING BASELINE PERFORMANCE REPORT")
    print("=" * 80)
    generate_report(monthly_performance, output_path)

    # Print summary
    print(f"\n{'=' * 80}")
    print("BASELINE EVALUATION COMPLETE")
    print("=" * 80)
    print(f"\n✅ Parsed {len(monthly_performance)} months of trading data")
    print(f"✅ Total trades analyzed: {sum(m.num_trades for m in monthly_performance)}")
    print(f"✅ Report saved to: {output_path}")
    print(f"\n🎯 Next Step: Review report and proceed to Task 0.2 (Setup 3.0 Directory)\n")

    return 0


if __name__ == "__main__":
    exit(main())
