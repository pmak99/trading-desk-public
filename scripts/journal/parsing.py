"""Column-matching and field-parsing helpers for Fidelity CSV exports."""

import re
from datetime import datetime
from typing import Dict, List, Optional


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
    clean = clean.replace('$', '').replace('€', '').replace('£', '')
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
