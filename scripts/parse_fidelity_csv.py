#!/usr/bin/env python3
"""
Parse Fidelity CSV exports (Realized Gains/Losses or Transaction History)
and correlate with VRP data from the ivcrush database.

Supports flexible column matching for different Fidelity export formats.
"""

import csv
import json
import sqlite3
import re
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from pathlib import Path


# Flexible column name mappings (Fidelity uses different names across exports)
COLUMN_MAPPINGS = {
    'symbol': ['Symbol', 'Symbol(CUSIP)', 'Security', 'Ticker', 'SYMBOL'],
    'description': ['Description', 'Security Description', 'DESCRIPTION'],
    'quantity': ['Quantity', 'Shares', 'Shares Sold', 'Qty', 'QUANTITY'],
    'acquired_date': ['Date Acquired', 'Acquired Date', 'Acquisition Date', 'Open Date', 'ACQUIRED DATE'],
    'sale_date': ['Date Sold', 'Sale Date', 'Close Date', 'Settlement Date', 'SALE DATE'],
    'cost_basis': ['Cost Basis', 'Cost', 'Adjusted Cost Basis', 'COST BASIS'],
    'proceeds': ['Proceeds', 'Sale Proceeds', 'Gross Proceeds', 'PROCEEDS'],
    'short_term_gl': ['Short Term Gain/Loss', 'Short-Term Gain/Loss', 'ST Gain/Loss'],
    'long_term_gl': ['Long Term Gain/Loss', 'Long-Term Gain/Loss', 'LT Gain/Loss'],
    'gain_loss': ['Gain/Loss', 'Realized Gain/Loss', 'Gain (Loss)', 'GAIN/LOSS', 'Realized G/L'],
    'term': ['Term', 'Holding Period', 'Short/Long', 'TERM'],
    'wash_sale': ['Wash Sale Disallowed', 'Disallowed Loss', 'Wash Sale Adjustment', 'WASH SALE'],
    'account': ['Account Number', 'Account', 'Acct', 'ACCOUNT'],
}


@dataclass
class Trade:
    """Represents a single closed trade"""
    symbol: str
    description: str
    quantity: int
    acquired_date: Optional[str]
    sale_date: str
    cost_basis: float
    proceeds: float
    gain_loss: float
    term: str  # SHORT or LONG
    wash_sale_amount: float = 0.0

    # Parsed option details
    option_type: Optional[str] = None  # PUT or CALL
    strike: Optional[float] = None
    expiration: Optional[str] = None
    underlying: Optional[str] = None

    # VRP correlation (populated later)
    earnings_date: Optional[str] = None
    vrp_ratio: Optional[float] = None
    implied_move: Optional[float] = None
    actual_move: Optional[float] = None
    beat_implied: Optional[bool] = None

    @property
    def is_option(self) -> bool:
        return self.option_type is not None

    @property
    def is_winner(self) -> bool:
        return self.gain_loss > 0

    @property
    def days_held(self) -> Optional[int]:
        if not self.acquired_date or not self.sale_date:
            return None
        try:
            acq = datetime.strptime(self.acquired_date, '%Y-%m-%d')
            sale = datetime.strptime(self.sale_date, '%Y-%m-%d')
            return (sale - acq).days
        except (ValueError, TypeError):
            return None

    @property
    def close_date(self) -> Optional[str]:
        # Credit trades: Fidelity inverts dates — acquired > sale means acquired is the close
        if self.acquired_date and self.sale_date and self.acquired_date > self.sale_date:
            return self.acquired_date
        return self.sale_date


@dataclass
class Strategy:
    """Represents a grouped multi-leg strategy for in-memory reporting only.

    strategy_type values: SINGLE, SPREAD, STOCK.
    STOCK is not DB-compatible (violates strategies CHECK constraint) — never
    pass Strategy objects directly to create_strategy_and_link or any DB write.
    """
    symbol: str
    strategy_type: str  # SINGLE, SPREAD, STOCK
    option_type: Optional[str]
    expiration: Optional[str]
    gain_loss: float
    legs: List['Trade']
    earnings_date: Optional[str] = None
    actual_move: Optional[float] = None

    @property
    def is_winner(self) -> bool:
        return self.gain_loss > 0

    @property
    def close_date(self) -> Optional[str]:
        dates = [l.close_date for l in self.legs if l.close_date]
        return max(dates) if dates else None


def find_column(headers: List[str], field_name: str) -> Optional[int]:
    """Find column index for a field using flexible matching.

    Handles variations in Fidelity exports:
    - Extra whitespace/padding
    - Different capitalization
    - Whitespace-collapsed comparison (e.g., "Date  Acquired" matches "Date Acquired")
    """
    possible_names = COLUMN_MAPPINGS.get(field_name, [field_name])

    for i, header in enumerate(headers):
        # Normalize: strip, collapse internal whitespace, lowercase
        header_normalized = ' '.join(header.strip().split()).lower()
        for name in possible_names:
            name_normalized = ' '.join(name.strip().split()).lower()
            if header_normalized == name_normalized:
                return i
    return None


def parse_money(value: str) -> float:
    """Parse money string like '$1,234.56' or '(1,234.56)' to float.

    Handles edge cases:
    - Parenthesized negatives: "(123.45)" -> -123.45
    - Dollar prefix: "$1,234.56" -> 1234.56
    - Leading minus with dollar: "-$1,234.56" -> -1234.56
    - Commas as thousands separators: "1,234.56" -> 1234.56
    - European format: "1.234,56" -> 1234.56
    - Currency codes: "USD 1,234.56" -> 1234.56
    """
    if not value or value.strip() in ('-', '', '--'):
        return 0.0

    value = value.strip()

    # Check for leading minus sign before currency symbol (e.g., "-$1,234.56")
    is_negative = False
    if value.startswith('-') and not value.startswith('-('):
        is_negative = True
        value = value[1:].strip()

    # Remove currency symbols and text prefixes
    clean = re.sub(r'[A-Z]{2,4}\s*', '', value.upper())  # Remove USD, EUR, etc.
    clean = clean.replace('$', '').replace('\u20ac', '').replace('\u00a3', '')
    clean = clean.replace(' ', '')

    # Handle parentheses for negative (check BEFORE removing commas)
    if clean.startswith('(') and clean.endswith(')'):
        is_negative = True
        clean = clean[1:-1]
    elif clean.startswith('-(') and clean.endswith(')'):
        is_negative = True
        clean = clean[2:-1]

    # Detect European format (1.234,56) vs US format (1,234.56)
    if ',' in clean and '.' in clean:
        if clean.rfind(',') > clean.rfind('.'):
            # European: 1.234,56
            clean = clean.replace('.', '').replace(',', '.')
        else:
            # US: 1,234.56
            clean = clean.replace(',', '')
    elif ',' in clean:
        # Only comma - could be European decimal OR US thousands
        parts = clean.split(',')
        if len(parts) == 2 and len(parts[1]) == 2:
            clean = clean.replace(',', '.')
        else:
            clean = clean.replace(',', '')

    # Remove any remaining non-numeric chars except . and -
    clean = re.sub(r'[^\d.\-]', '', clean)

    try:
        amount = float(clean)
        return -abs(amount) if is_negative else amount
    except ValueError:
        return 0.0


def parse_date(value: str) -> Optional[str]:
    """Parse date string to YYYY-MM-DD format"""
    if not value or value.strip() in ('-', '', '--'):
        return None

    # Strip any time component first
    value_clean = value.strip().split('T')[0].split(' ')[0]

    # Try common formats
    formats = [
        '%m/%d/%Y',
        '%Y-%m-%d',
        '%m-%d-%Y',
        '%m/%d/%y',
        '%Y/%m/%d',
        '%d/%m/%Y',      # European format
        '%Y%m%d',        # Compact format
        '%m-%d-%y',      # Two-digit year with dashes
        '%d-%m-%Y',      # European with dashes
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(value_clean, fmt)
            # Validate year is reasonable (between 2000-2100)
            if 2000 <= dt.year <= 2100:
                return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue

    return None


def parse_option_description(description: str) -> Dict:
    """Extract option details from Fidelity description.

    Handles multiple formats:
    - "PUT (NVDA) NVIDIA CORP JAN 17 25 $150.00"  (standard Fidelity)
    - "NVDA 02/07/2026 150.00 C"                  (compact format)
    - "CALL (AAPL) APPLE INC FEB 21 2025 $225"    (long year)

    Returns dict with option_type, underlying, strike, expiration (or None for each).
    Logs a warning if option type is detected but other fields cannot be parsed.
    """
    result = {
        'option_type': None,
        'underlying': None,
        'strike': None,
        'expiration': None,
    }

    if not description:
        return result

    desc_upper = description.upper().strip()

    # Detect option type
    if 'PUT' in desc_upper:
        result['option_type'] = 'PUT'
    elif 'CALL' in desc_upper:
        result['option_type'] = 'CALL'
    else:
        # Try compact format: "NVDA 02/07/2026 150.00 C" or "NVDA 02/07/2026 150.00 P"
        compact_match = re.match(
            r'^([A-Z][A-Z0-9]{0,5})\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+(\d+(?:\.\d+)?)\s+([CP])$',
            desc_upper
        )
        if compact_match:
            result['underlying'] = compact_match.group(1)
            result['option_type'] = 'CALL' if compact_match.group(4) == 'C' else 'PUT'
            result['strike'] = float(compact_match.group(3))
            # Parse date
            exp_date = parse_date(compact_match.group(2))
            if exp_date:
                result['expiration'] = exp_date
            return result
        return result  # Not an option

    # Extract underlying - usually in parentheses like "PUT (NVDA)"
    # Allow up to 6 chars and optional numbers for tickers like BRK.B -> BRKB
    match = re.search(r'(?:PUT|CALL)\s*\(([A-Z][A-Z0-9]{0,5})\)', desc_upper)
    if match:
        result['underlying'] = match.group(1)
    else:
        # Try to find ticker at start
        match = re.search(r'^([A-Z][A-Z0-9]{0,5})\s', desc_upper)
        if match:
            result['underlying'] = match.group(1)

    # Extract strike price
    match = re.search(r'\$(\d+(?:\.\d+)?)', description)
    if match:
        result['strike'] = float(match.group(1))

    # Extract expiration date
    month_map = {
        'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04',
        'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08',
        'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
    }

    # Try "JAN 17 25" or "JAN 17 2025" format
    match = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})\s+(\d{2,4})', desc_upper)
    if match:
        month = month_map[match.group(1)]
        day = match.group(2).zfill(2)
        year = match.group(3)
        if len(year) == 2:
            year = '20' + year
        result['expiration'] = f"{year}-{month}-{day}"

    # Log warning if option type detected but key fields are missing
    if result['option_type'] and not result['underlying']:
        import sys
        print(f"      Warning: Option detected but could not parse underlying from: {description[:80]}", file=sys.stderr)

    return result


def parse_occ_symbol(symbol: str) -> Dict:
    """Parse OCC option symbol format like 'AAPL250117C00150000' or 'ACN250926P215'"""
    result = {
        'option_type': None,
        'underlying': None,
        'strike': None,
        'expiration': None,
    }

    if not symbol:
        return result

    # OCC format: TICKER{6-digit date YYMMDD}[P|C]{strike}
    # Examples: "AAPL250117C00150000", "ACN250926P215(8061839XV)"
    # Remove any CUSIP suffix in parentheses
    symbol_clean = re.sub(r'\([^)]+\)$', '', symbol.strip())

    occ_match = re.match(r'^([A-Z][A-Z0-9]{0,5})(\d{6})([PC])(\d+)', symbol_clean)
    if occ_match:
        result['underlying'] = occ_match.group(1)
        result['option_type'] = 'PUT' if occ_match.group(3) == 'P' else 'CALL'

        # Parse date from YYMMDD
        date_str = occ_match.group(2)
        try:
            year = '20' + date_str[:2]
            month = date_str[2:4]
            day = date_str[4:6]
            result['expiration'] = f"{year}-{month}-{day}"
        except (IndexError, ValueError):
            pass

        # Parse strike - OCC format always has strike * 1000 (8-digit right-padded)
        # Examples: 00150000 = $150, 00620000 = $620, 06850000 = $6850
        try:
            strike_code = occ_match.group(4)
            strike_val = float(strike_code)
            # OCC format: always divide by 1000
            result['strike'] = strike_val / 1000
        except (ValueError, TypeError):
            pass

    return result


def parse_fidelity_csv(filepath: str) -> Tuple[List[Trade], List[Tuple], List[dict]]:
    """Parse Fidelity CSV export into Trade objects

    Returns:
        Tuple of (trades, skipped_rows, unable_rows) where:
        - skipped_rows: (row_num, reason, row_preview) for debugging
        - unable_rows: positions Fidelity could not export G/L for
    """
    trades = []
    skipped_rows = []
    unable_rows = []

    # Read file efficiently - peek at first 20 lines to find header
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        peek_lines = []
        for i, line in enumerate(f):
            peek_lines.append(line)
            if i >= 20:
                break

        # Find header index
        header_idx = 0
        for i, line in enumerate(peek_lines):
            if any(col.lower() in line.lower() for col in ['symbol', 'description', 'proceeds']):
                header_idx = i
                break

        # Reset and skip to header
        f.seek(0)
        for _ in range(header_idx):
            next(f)

        reader = csv.reader(f)
        headers = next(reader)

        # Find column indices
        col_idx = {}
        for field_name in COLUMN_MAPPINGS.keys():
            idx = find_column(headers, field_name)
            col_idx[field_name] = idx

        print(f"      Column mapping: {[(k, v) for k, v in col_idx.items() if v is not None]}")

        # Parse rows
        row_num = header_idx + 1
        for row in reader:
            row_num += 1

            if not row or len(row) < 3:
                skipped_rows.append((row_num, 'Insufficient columns', None))
                continue

            # Skip summary/total rows and wash sale adjustment rows
            first_col = row[0].strip() if row else ''
            if not first_col:
                skipped_rows.append((row_num, 'Empty first column', None))
                continue

            row_text = str(row).lower()
            skip_terms = ['total', 'subtotal', 'disclaimer', 'wash sale']
            if any(skip in row_text for skip in skip_terms):
                matched_term = [s for s in skip_terms if s in row_text][0]
                skipped_rows.append((row_num, f'Summary row ({matched_term})', None))
                continue

            # Detect Fidelity "unable to provide" rows — real positions with missing G/L data
            if len(row) == 3 and 'unable' in row[2].lower():
                desc = row[1].strip() if len(row) > 1 else ''
                opt = parse_option_description(desc)
                unable_rows.append({
                    'cusip': row[0].strip(),
                    'description': desc,
                    'symbol': opt.get('underlying', '?'),
                    'option_type': opt.get('option_type', ''),
                    'strike': opt.get('strike', ''),
                    'expiration': opt.get('expiration', ''),
                })
                continue

            def get_val(field: str) -> str:
                idx = col_idx.get(field)
                if idx is not None and idx < len(row):
                    return row[idx].strip()
                return ''

            raw_symbol = get_val('symbol')
            if not raw_symbol:
                skipped_rows.append((row_num, 'No symbol found', row[:5] if len(row) >= 5 else row))
                continue

            description = get_val('description')

            # Parse option details from description first
            opt_details = parse_option_description(description)

            # If description parsing didn't get underlying, try OCC symbol format
            if not opt_details['underlying']:
                occ_details = parse_occ_symbol(raw_symbol)
                # Merge OCC details if found
                if occ_details['underlying']:
                    for key, val in occ_details.items():
                        if val is not None and opt_details.get(key) is None:
                            opt_details[key] = val

            # Determine final symbol
            if opt_details['underlying']:
                symbol = opt_details['underlying']
            else:
                # Not an option, use raw symbol (strip any suffixes)
                symbol = re.sub(r'\([^)]+\)$', '', raw_symbol).strip()
                # Also strip any numeric suffixes for stocks
                match = re.match(r'^([A-Z][A-Z0-9]{0,5})', symbol)
                if match:
                    symbol = match.group(1)

            # Parse quantity
            qty_str = get_val('quantity')
            try:
                qty_float = float(qty_str.replace(',', '').strip()) if qty_str else 0
                quantity = abs(int(qty_float)) if qty_float == int(qty_float) else abs(qty_float)
            except (ValueError, AttributeError):
                quantity = 0

            # Parse dates
            acquired_date = parse_date(get_val('acquired_date'))
            sale_date = parse_date(get_val('sale_date'))

            if not sale_date:
                skipped_rows.append((row_num, 'No sale date', row[:5] if len(row) >= 5 else row))
                continue

            # Parse money fields
            cost_basis = parse_money(get_val('cost_basis'))
            proceeds = parse_money(get_val('proceeds'))

            # Try short/long term specific columns first, then fall back to combined
            short_term_gl = parse_money(get_val('short_term_gl'))
            long_term_gl = parse_money(get_val('long_term_gl'))

            if short_term_gl != 0 or long_term_gl != 0:
                gain_loss = short_term_gl + long_term_gl
                term = 'LONG' if long_term_gl != 0 else 'SHORT'
            else:
                gain_loss = parse_money(get_val('gain_loss'))
                term_str = get_val('term').upper()
                term = 'LONG' if 'LONG' in term_str else 'SHORT'

            wash_sale = parse_money(get_val('wash_sale'))

            trade = Trade(
                symbol=symbol,
                description=description,
                quantity=quantity,
                acquired_date=acquired_date,
                sale_date=sale_date,
                cost_basis=cost_basis,
                proceeds=proceeds,
                gain_loss=gain_loss,
                term=term,
                wash_sale_amount=wash_sale,
                option_type=opt_details['option_type'],
                strike=opt_details['strike'],
                expiration=opt_details['expiration'],
                underlying=opt_details['underlying'],
            )
            trades.append(trade)

    return trades, skipped_rows, unable_rows


def load_historical_moves(db_path: str, min_date: str = '2020-01-01') -> Dict[str, List[Dict]]:
    """Load historical moves from ivcrush database, grouped by ticker

    Args:
        db_path: Path to ivcrush.db
        min_date: Only load earnings after this date (default 2020-01-01)
    """
    moves_by_ticker = defaultdict(list)

    if not os.path.exists(db_path):
        print(f"      Warning: Database not found at {db_path}")
        return moves_by_ticker

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ticker, earnings_date, gap_move_pct, intraday_move_pct
        FROM historical_moves
        WHERE earnings_date >= ?
        ORDER BY ticker, earnings_date
    """, (min_date,))

    for row in cursor.fetchall():
        ticker, earnings_date, gap_move, intraday_move = row
        moves_by_ticker[ticker].append({
            'earnings_date': earnings_date,
            'gap_move_pct': gap_move,
            'intraday_move_pct': intraday_move,
            'actual_move': max(abs(gap_move), abs(intraday_move)),
        })

    conn.close()
    return moves_by_ticker


def find_nearest_earnings(trade: Trade, moves_by_ticker: Dict) -> Optional[Dict]:
    """Find the earnings event that this trade straddles (for IV crush strategy)

    For IV crush: position opened BEFORE earnings, closed AFTER earnings.
    This function validates that the trade window brackets an earnings date.
    """
    ticker = trade.symbol
    if ticker not in moves_by_ticker:
        return None

    try:
        # Use normalised dates: Fidelity inverts acquired/sale for credit trades
        # (sale_date = when opened, acquired_date = when closed).
        # Trade.close_date handles this inversion; open_date is the other field.
        close_date_str = trade.close_date  # actual close, regardless of trade direction
        open_date_str = (
            trade.sale_date
            if close_date_str == trade.acquired_date
            else trade.acquired_date
        )
        close_dt = datetime.strptime(close_date_str, '%Y-%m-%d')
        open_dt = datetime.strptime(open_date_str, '%Y-%m-%d') if open_date_str else None
    except (ValueError, TypeError):
        return None

    best_match = None
    min_diff = float('inf')

    for move in moves_by_ticker[ticker]:
        try:
            earnings = datetime.strptime(move['earnings_date'], '%Y-%m-%d')
        except ValueError:
            continue

        if open_dt:
            # IV crush: opened before earnings, closed after
            days_before = (earnings - open_dt).days
            days_after = (close_dt - earnings).days

            if 0 <= days_before <= 7 and 0 <= days_after <= 7:
                diff = abs(days_after)
                if diff < min_diff:
                    min_diff = diff
                    best_match = move
        else:
            # No open date — loose match: closed 0-3 days after earnings
            days_after = (close_dt - earnings).days
            if 0 <= days_after <= 3:
                if days_after < min_diff:
                    min_diff = days_after
                    best_match = move

    return best_match


def correlate_with_vrp(trades: List[Trade], db_path: str) -> Tuple[List[Trade], int]:
    """Add VRP correlation data to trades

    Returns:
        Tuple of (trades, matched_count)
    """
    moves_by_ticker = load_historical_moves(db_path)
    matched_count = 0

    for trade in trades:
        if not trade.is_option:
            continue

        match = find_nearest_earnings(trade, moves_by_ticker)
        if match:
            trade.earnings_date = match['earnings_date']
            trade.actual_move = match['actual_move']
            matched_count += 1

    return trades, matched_count


def group_trades_into_strategies(trades: List[Trade]) -> List[Strategy]:
    """
    Group individual trade legs into strategies by (symbol, expiration, option_type).

    One distinct strike → SINGLE. Two → paired SPREADs. Three or more → single
    SPREAD grouping all legs (avoids silently dropping middle-strike legs).

    Note: iron condors cannot be detected here — grouping by option_type means
    PUT and CALL legs always land in separate groups. They appear as two SPREADs.
    For in-memory reporting only; do not persist Strategy objects to the DB.
    """
    options = [t for t in trades if t.is_option]
    stocks = [t for t in trades if not t.is_option]

    groups: Dict[tuple, List[Trade]] = defaultdict(list)
    for t in options:
        key = (t.symbol, t.expiration or '', t.option_type or '')
        groups[key].append(t)

    strategies: List[Strategy] = []

    for (symbol, expiration, option_type), legs in groups.items():
        strikes = sorted(set(l.strike for l in legs if l.strike))
        n_strikes = len(strikes)

        if n_strikes <= 1:
            # Multiple fills of the same single — one strategy per strike group
            by_strike: Dict = defaultdict(list)
            for l in legs:
                by_strike[l.strike].append(l)
            for strike_legs in by_strike.values():
                pnl = sum(l.gain_loss for l in strike_legs)
                strategies.append(Strategy(
                    symbol=symbol, strategy_type='SINGLE',
                    option_type=option_type or None,
                    expiration=expiration or None, gain_loss=pnl,
                    legs=strike_legs,
                    earnings_date=strike_legs[0].earnings_date,
                    actual_move=strike_legs[0].actual_move,
                ))
        elif n_strikes == 2:
            # SPREAD: pair legs by matching strike quantities
            by_strike = defaultdict(list)
            for l in legs:
                by_strike[l.strike].append(l)
            low_legs = by_strike[strikes[0]]
            high_legs = by_strike[strikes[1]]
            if len(low_legs) == len(high_legs):
                for low, high in zip(low_legs, high_legs):
                    pair = [low, high]
                    pnl = sum(l.gain_loss for l in pair)
                    strategies.append(Strategy(
                        symbol=symbol, strategy_type='SPREAD',
                        option_type=option_type or None,
                        expiration=expiration or None, gain_loss=pnl,
                        legs=pair,
                        earnings_date=low.earnings_date or high.earnings_date,
                        actual_move=low.actual_move or high.actual_move,
                    ))
            else:
                # Unequal leg counts — group all together
                pnl = sum(l.gain_loss for l in legs)
                strategies.append(Strategy(
                    symbol=symbol, strategy_type='SPREAD',
                    option_type=option_type or None,
                    expiration=expiration or None, gain_loss=pnl,
                    legs=legs,
                    earnings_date=legs[0].earnings_date,
                    actual_move=legs[0].actual_move,
                ))
        else:
            # 3+ distinct strikes (partial condor, broken spread, etc.) —
            # group all legs together to avoid silently dropping middle strikes
            pnl = sum(l.gain_loss for l in legs)
            strategies.append(Strategy(
                symbol=symbol, strategy_type='SPREAD',
                option_type=option_type or None,
                expiration=expiration or None, gain_loss=pnl,
                legs=legs,
                earnings_date=legs[0].earnings_date,
                actual_move=legs[0].actual_move,
            ))

    for t in stocks:
        strategies.append(Strategy(
            symbol=t.symbol, strategy_type='STOCK',
            option_type=None, expiration=None,
            gain_loss=t.gain_loss, legs=[t],
            earnings_date=t.earnings_date, actual_move=t.actual_move,
        ))

    return strategies


def calculate_strategy_statistics(strategies: List[Strategy]) -> Dict:
    """Calculate statistics at the strategy level (spreads counted as one trade)."""
    total = len(strategies)
    if total == 0:
        return {}

    winners = [s for s in strategies if s.is_winner]
    losers = [s for s in strategies if not s.is_winner]
    options = [s for s in strategies if s.strategy_type != 'STOCK']
    stocks = [s for s in strategies if s.strategy_type == 'STOCK']

    total_pnl = sum(s.gain_loss for s in strategies)
    winner_pnl = sum(s.gain_loss for s in winners)
    loser_pnl = sum(s.gain_loss for s in losers)

    by_ticker: Dict = defaultdict(lambda: {'count': 0, 'pnl': 0.0, 'wins': 0})
    for s in strategies:
        by_ticker[s.symbol]['count'] += 1
        by_ticker[s.symbol]['pnl'] += s.gain_loss
        if s.is_winner:
            by_ticker[s.symbol]['wins'] += 1

    by_month: Dict = defaultdict(lambda: {'count': 0, 'pnl': 0.0, 'wins': 0})
    for s in strategies:
        cd = s.close_date or ''
        month = cd[:7] if len(cd) >= 7 else 'UNKNOWN'
        by_month[month]['count'] += 1
        by_month[month]['pnl'] += s.gain_loss
        if s.is_winner:
            by_month[month]['wins'] += 1

    by_option_type: Dict = defaultdict(lambda: {'count': 0, 'pnl': 0.0, 'wins': 0})
    for s in options:
        otype = s.option_type or 'UNKNOWN'
        by_option_type[otype]['count'] += 1
        by_option_type[otype]['pnl'] += s.gain_loss
        if s.is_winner:
            by_option_type[otype]['wins'] += 1

    by_strategy_type: Dict = defaultdict(lambda: {'count': 0, 'pnl': 0.0, 'wins': 0})
    for s in strategies:
        by_strategy_type[s.strategy_type]['count'] += 1
        by_strategy_type[s.strategy_type]['pnl'] += s.gain_loss
        if s.is_winner:
            by_strategy_type[s.strategy_type]['wins'] += 1

    earnings_strategies = [s for s in strategies if s.earnings_date]

    return {
        'total_trades': total,
        'winners': len(winners),
        'losers': len(losers),
        'win_rate': round(100 * len(winners) / total, 1) if total > 0 else 0,
        'total_pnl': round(total_pnl, 2),
        'winner_pnl': round(winner_pnl, 2),
        'loser_pnl': round(loser_pnl, 2),
        'avg_win': round(winner_pnl / len(winners), 2) if winners else 0,
        'avg_loss': round(loser_pnl / len(losers), 2) if losers else 0,
        'profit_factor': round(abs(winner_pnl / loser_pnl), 2) if loser_pnl else 0,
        'options_count': len(options),
        'stocks_count': len(stocks),
        'by_ticker': {k: {
            'count': v['count'],
            'pnl': round(v['pnl'], 2),
            'win_rate': round(100 * v['wins'] / v['count'], 1) if v['count'] > 0 else 0,
        } for k, v in sorted(by_ticker.items(), key=lambda x: x[1]['pnl'], reverse=True)},
        'by_month': {k: {
            'count': v['count'],
            'pnl': round(v['pnl'], 2),
            'win_rate': round(100 * v['wins'] / v['count'], 1) if v['count'] > 0 else 0,
        } for k, v in sorted(by_month.items())},
        'by_option_type': {k: dict(v) for k, v in by_option_type.items()},
        'by_strategy_type': {k: {
            'count': v['count'],
            'pnl': round(v['pnl'], 2),
            'win_rate': round(100 * v['wins'] / v['count'], 1) if v['count'] > 0 else 0,
        } for k, v in sorted(by_strategy_type.items())},
        'earnings_correlated': len(earnings_strategies),
        'wash_sales': {'count': 0, 'total': 0.0},
    }


def calculate_statistics(trades: List[Trade]) -> Dict:
    """Calculate comprehensive trading statistics"""

    total = len(trades)
    if total == 0:
        return {}

    winners = [t for t in trades if t.is_winner]
    losers = [t for t in trades if not t.is_winner]
    options = [t for t in trades if t.is_option]
    stocks = [t for t in trades if not t.is_option]

    total_pnl = sum(t.gain_loss for t in trades)
    winner_pnl = sum(t.gain_loss for t in winners)
    loser_pnl = sum(t.gain_loss for t in losers)

    # By ticker
    by_ticker = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})
    for t in trades:
        by_ticker[t.symbol]['count'] += 1
        by_ticker[t.symbol]['pnl'] += t.gain_loss
        if t.is_winner:
            by_ticker[t.symbol]['wins'] += 1

    # By month — use the CLOSE date for bucketing.
    # For credit trades (sell-to-open), Fidelity inverts the dates:
    # "Date Acquired" = when the short was covered (close), "Date Sold" = when opened.
    # So for credit trades where acquired_date > sale_date, use acquired_date.
    by_month = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})
    for t in trades:
        close_date = t.sale_date
        if t.acquired_date and t.sale_date and t.acquired_date > t.sale_date:
            close_date = t.acquired_date  # credit trade: close = acquired_date
        if close_date and len(close_date) >= 7:
            month = close_date[:7]
        else:
            month = 'UNKNOWN'
        by_month[month]['count'] += 1
        by_month[month]['pnl'] += t.gain_loss
        if t.is_winner:
            by_month[month]['wins'] += 1

    # By option type
    by_option_type = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})
    for t in options:
        otype = t.option_type or 'UNKNOWN'
        by_option_type[otype]['count'] += 1
        by_option_type[otype]['pnl'] += t.gain_loss
        if t.is_winner:
            by_option_type[otype]['wins'] += 1

    # Earnings correlation stats
    earnings_trades = [t for t in trades if t.earnings_date]

    return {
        'total_trades': total,
        'winners': len(winners),
        'losers': len(losers),
        'win_rate': round(100 * len(winners) / total, 1) if total > 0 else 0,
        'total_pnl': round(total_pnl, 2),
        'winner_pnl': round(winner_pnl, 2),
        'loser_pnl': round(loser_pnl, 2),
        'avg_win': round(winner_pnl / len(winners), 2) if winners else 0,
        'avg_loss': round(loser_pnl / len(losers), 2) if losers else 0,
        'profit_factor': round(abs(winner_pnl / loser_pnl), 2) if loser_pnl else 0,
        'options_count': len(options),
        'stocks_count': len(stocks),
        'by_ticker': {k: {
            'count': v['count'],
            'pnl': round(v['pnl'], 2),
            'win_rate': round(100 * v['wins'] / v['count'], 1) if v['count'] > 0 else 0
        } for k, v in sorted(by_ticker.items(), key=lambda x: x[1]['pnl'], reverse=True)},
        'by_month': {k: {
            'count': v['count'],
            'pnl': round(v['pnl'], 2),
            'win_rate': round(100 * v['wins'] / v['count'], 1) if v['count'] > 0 else 0
        } for k, v in sorted(by_month.items())},
        'by_option_type': dict(by_option_type),
        'earnings_correlated': len(earnings_trades),
        'wash_sales': {
            'count': len([t for t in trades if t.wash_sale_amount != 0]),
            'total': round(sum(t.wash_sale_amount for t in trades), 2),
        }
    }


def export_journal_csv(trades: List[Trade], filepath: str):
    """Export trades to enhanced CSV format"""

    fieldnames = [
        'Sale Date', 'Acquired Date', 'Days Held', 'Symbol', 'Type',
        'Option Type', 'Strike', 'Expiration', 'Quantity',
        'Cost Basis', 'Proceeds', 'Gain/Loss', 'Winner', 'Term',
        'Wash Sale', 'Earnings Date', 'Actual Move %'
    ]

    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for t in sorted(trades, key=lambda x: x.sale_date):
            writer.writerow({
                'Sale Date': t.sale_date,
                'Acquired Date': t.acquired_date or '',
                'Days Held': t.days_held or '',
                'Symbol': t.symbol,
                'Type': 'OPTION' if t.is_option else 'STOCK',
                'Option Type': t.option_type or '',
                'Strike': t.strike or '',
                'Expiration': t.expiration or '',
                'Quantity': t.quantity,
                'Cost Basis': f"${t.cost_basis:,.2f}",
                'Proceeds': f"${t.proceeds:,.2f}",
                'Gain/Loss': f"${t.gain_loss:,.2f}",
                'Winner': 'YES' if t.is_winner else 'NO',
                'Term': t.term,
                'Wash Sale': f"${t.wash_sale_amount:,.2f}" if t.wash_sale_amount else '',
                'Earnings Date': t.earnings_date or '',
                'Actual Move %': f"{t.actual_move:.1f}%" if t.actual_move else '',
            })


def print_summary(stats: Dict, skipped_count: int = 0, unable_rows: list = None):
    """Print formatted summary"""

    print("\n" + "=" * 70)
    print("TRADING JOURNAL SUMMARY")
    print("=" * 70)

    print(f"\n   OVERALL PERFORMANCE")
    print(f"   Total Trades:    {stats['total_trades']}")
    print(f"   Win Rate:        {stats['win_rate']}%")
    print(f"   Winners:         {stats['winners']}")
    print(f"   Losers:          {stats['losers']}")

    print(f"\n   PROFIT & LOSS")
    print(f"   Total P&L:       ${stats['total_pnl']:,.2f}")
    print(f"   From Winners:    ${stats['winner_pnl']:,.2f}")
    print(f"   From Losers:     ${stats['loser_pnl']:,.2f}")
    print(f"   Avg Win:         ${stats['avg_win']:,.2f}")
    print(f"   Avg Loss:        ${stats['avg_loss']:,.2f}")
    print(f"   Profit Factor:   {stats['profit_factor']}")

    print(f"\n   BY INSTRUMENT")
    print(f"   Options:         {stats['options_count']}")
    print(f"   Stocks:          {stats['stocks_count']}")

    if stats.get('by_strategy_type'):
        print(f"\n   BY STRATEGY TYPE")
        for stype, data in stats['by_strategy_type'].items():
            print(f"   {stype:12} {data['count']:4} trades  {data['win_rate']:5.1f}% win  ${data['pnl']:>12,.2f}")

    if stats['by_option_type']:
        print(f"\n   BY OPTION TYPE")
        for otype, data in stats['by_option_type'].items():
            win_rate = 100 * data['wins'] / data['count'] if data['count'] > 0 else 0
            print(f"   {otype:8} {data['count']:4} trades  {win_rate:5.1f}% win  ${data['pnl']:>12,.2f}")

    print(f"\n   TOP 5 TICKERS BY P&L")
    for i, (ticker, data) in enumerate(list(stats['by_ticker'].items())[:5]):
        print(f"   {ticker:8} {data['count']:3} trades  {data['win_rate']:5.1f}% win  ${data['pnl']:>12,.2f}")

    print(f"\n   MONTHLY P&L")
    ytd = 0
    current_year = None
    for month, data in stats['by_month'].items():
        # Determine year from month string (format: YYYY-MM)
        month_year = int(month.split('-')[0]) if '-' in month else None
        # Reset YTD at start of each new calendar year
        if month_year and month_year != current_year:
            current_year = month_year
            ytd = 0
        ytd += data['pnl']
        print(f"   {month}  {data['count']:3} trades  {data['win_rate']:5.1f}% win  ${data['pnl']:>10,.2f}  (YTD: ${ytd:>12,.2f})")

    if stats['earnings_correlated'] > 0:
        print(f"\n   EARNINGS CORRELATION")
        print(f"   Trades matched to earnings: {stats['earnings_correlated']}")

    if stats['wash_sales']['count'] > 0:
        print(f"\n   WASH SALES")
        print(f"   Count:           {stats['wash_sales']['count']}")
        print(f"   Disallowed:      ${stats['wash_sales']['total']:,.2f}")

    if unable_rows:
        print(f"\n   *** DATA GAP — FIDELITY MISSING G/L FOR {len(unable_rows)} POSITIONS ***")
        print(f"   These positions were closed but Fidelity could not export gain/loss.")
        print(f"   Totals above are UNDERSTATED. Verify these in Fidelity directly:\n")
        for r in unable_rows:
            exp = r['expiration'] or '?'
            strike = r['strike'] or '?'
            otype = r['option_type'] or '?'
            print(f"   {r['symbol']:6}  {otype:4}  strike {strike}  exp {exp}")
            print(f"          Description: {r['description']}")
        print()

    if skipped_count > 0:
        print(f"\n   PARSING NOTES")
        print(f"   Rows skipped:    {skipped_count}")


def main():
    import argparse

    # Get project root for portable paths
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    home = Path.home()

    parser = argparse.ArgumentParser(description='Parse Fidelity CSV exports')
    parser.add_argument('csv_file', nargs='?', help='Path to Fidelity CSV export')
    parser.add_argument('--db', default=str(project_root / '2.0' / 'data' / 'ivcrush.db'),
                       help='Path to ivcrush database for VRP correlation')
    parser.add_argument('--output', default=str(project_root / 'docs' / '2025 Trades'),
                       help='Output directory')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Show skipped rows details')

    args = parser.parse_args()

    # Find CSV file
    if args.csv_file:
        csv_path = args.csv_file
    else:
        # Look for CSV in common locations, prefer most recent
        search_paths = [
            home / 'Downloads',
            home / 'Desktop',
            Path(args.output),
        ]
        csv_candidates = []

        for search_dir in search_paths:
            if search_dir.exists():
                for f in search_dir.iterdir():
                    if not f.suffix.lower() == '.csv':
                        continue
                    fname_lower = f.name.lower()
                    if any(term in fname_lower for term in ['fidelity', 'gain', 'realized', 'portfolio', 'closed']):
                        mtime = f.stat().st_mtime
                        csv_candidates.append((mtime, str(f)))

        # Pick most recent
        csv_path = max(csv_candidates, key=lambda x: x[0])[1] if csv_candidates else None

    if not csv_path or not os.path.exists(csv_path):
        print("No Fidelity CSV file found.")
        print("\nTo use this parser:")
        print("1. Log into Fidelity.com")
        print("2. Go to Accounts & Trade -> Tax Information")
        print("3. Select 'Realized Gain/Loss' for your account")
        print("4. Click 'Download' or 'CSV' to export")
        print("5. Run: python parse_fidelity_csv.py /path/to/downloaded.csv")
        return

    print("=" * 70)
    print("FIDELITY CSV JOURNAL PARSER")
    print("=" * 70)
    print(f"\nInput:  {csv_path}")

    # Parse CSV
    print("\n[1/4] Parsing Fidelity CSV...")
    trades, skipped_rows, unable_rows = parse_fidelity_csv(csv_path)
    print(f"      Found {len(trades)} trades")
    if unable_rows:
        print(f"      WARNING: {len(unable_rows)} positions missing G/L data (Fidelity export error)")

    if skipped_rows and args.verbose:
        print(f"      Skipped {len(skipped_rows)} rows:")
        for num, reason, data in skipped_rows[:10]:
            print(f"        Row {num}: {reason}")
        if len(skipped_rows) > 10:
            print(f"        ... and {len(skipped_rows) - 10} more")

    # Correlate with VRP
    print("\n[2/4] Correlating with earnings data...")
    trades, correlated = correlate_with_vrp(trades, args.db)
    print(f"      Matched {correlated} trades to earnings events")

    # Calculate stats
    print("\n[3/4] Calculating statistics...")
    strategies = group_trades_into_strategies(trades)
    singles = sum(1 for s in strategies if s.strategy_type == 'SINGLE')
    spreads = sum(1 for s in strategies if s.strategy_type == 'SPREAD')
    condors = sum(1 for s in strategies if s.strategy_type == 'IRON_CONDOR')
    print(f"      Grouped {len(trades)} legs → {len(strategies)} strategies"
          f"  ({singles} single, {spreads} spread"
          + (f", {condors} condor" if condors else "") + ")")
    stats = calculate_strategy_statistics(strategies)

    # Print summary
    print_summary(stats, len(skipped_rows), unable_rows)

    # Export files
    print("\n[4/4] Exporting files...")

    os.makedirs(args.output, exist_ok=True)

    csv_out = os.path.join(args.output, 'trading_journal_enhanced.csv')
    export_journal_csv(trades, csv_out)
    print(f"      CSV:  {csv_out}")

    json_out = os.path.join(args.output, 'trading_journal_enhanced.json')
    with open(json_out, 'w') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'source_file': csv_path,
            'statistics': stats,
            'trades': [
                {
                    'sale_date': t.sale_date,
                    'acquired_date': t.acquired_date,
                    'days_held': t.days_held,
                    'symbol': t.symbol,
                    'is_option': t.is_option,
                    'option_type': t.option_type,
                    'strike': t.strike,
                    'expiration': t.expiration,
                    'quantity': t.quantity,
                    'cost_basis': t.cost_basis,
                    'proceeds': t.proceeds,
                    'gain_loss': t.gain_loss,
                    'is_winner': t.is_winner,
                    'term': t.term,
                    'wash_sale_amount': t.wash_sale_amount,
                    'earnings_date': t.earnings_date,
                    'actual_move': t.actual_move,
                }
                for t in sorted(trades, key=lambda x: x.sale_date)
            ]
        }, f, indent=2)
    print(f"      JSON: {json_out}")

    print("\nDone!")


if __name__ == "__main__":
    main()
