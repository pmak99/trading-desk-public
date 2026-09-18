"""Read a Fidelity CSV export into a list of Trade objects."""

import csv
import re
from typing import List, Tuple

from journal.models import Trade
from journal.parsing import (
    COLUMN_MAPPINGS,
    find_column,
    parse_date,
    parse_money,
    parse_occ_symbol,
    parse_option_description,
)


def _row_reader(row: List[str], col_idx: dict):
    """One row's cells by field name: stripped, or '' when the column is absent
    or the row is short. Bound to the row it is built from -- the two closures it
    replaced were defined inside the row loop and read `row` late (ruff B023),
    harmless only while every call ran in the iteration that defined them."""
    def get_val(field: str) -> str:
        idx = col_idx.get(field)
        return row[idx].strip() if idx is not None and idx < len(row) else ''
    return get_val


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
        last_trade = None
        for row in reader:
            row_num += 1

            if not row or len(row) < 3:
                skipped_rows.append((row_num, 'Insufficient columns', None))
                continue

            # ⚠️ A wash-sale adjustment is its own row under the lot it adjusts:
            # no symbol, "Wash Sale" in a date column, the disallowed loss as a
            # negative cost basis and a positive gain. Fold it into that lot.
            # Skipping it (as this reader did until 2026-09-13) left Fidelity's
            # tax-lot figure, whose cost basis already carries losses disallowed
            # on earlier sales -- taxable 2025 losses came out materially larger
            # than the statements' cash. Lot + wash rows = cash, to the cent.
            get_val = _row_reader(row, col_idx)
            if any(cell.strip().lower() == 'wash sale' for cell in row):
                if last_trade is None:
                    skipped_rows.append((row_num, 'Wash sale row with no lot above it', row[:9]))
                    continue
                wash_cost = parse_money(get_val('cost_basis'))
                wash_gain = parse_money(get_val('short_term_gl')) + parse_money(get_val('long_term_gl'))
                last_trade.cost_basis += wash_cost
                last_trade.gain_loss += wash_gain
                last_trade.wash_sale_amount += wash_gain
                continue

            # Skip summary/total rows (wash sale rows are handled above)
            first_col = row[0].strip() if row else ''
            if not first_col:
                skipped_rows.append((row_num, 'Empty first column', None))
                last_trade = None
                continue

            # A summary row names itself in the first cell ("Total", "Subtotal"). Matching
            # 'total' anywhere in the row dropped every TotalEnergies (TTE) lot as one.
            summary = re.match(r'(sub)?total\b', first_col.lower())
            if summary:
                skipped_rows.append((row_num, f'Summary row ({summary.group(0)})', None))
                continue
            if 'disclaimer' in str(row).lower():
                skipped_rows.append((row_num, 'Summary row (disclaimer)', None))
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
            last_trade = trade

    return trades, skipped_rows, unable_rows
