"""Characterization tests for the Fidelity CSV parsing helpers.

These pin down behavior of scripts/journal/parsing.py. Any failure after
the god-file split is a regression, not an expected diff.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from journal.parsing import (
    find_column,
    parse_money,
    parse_date,
    parse_option_description,
    parse_occ_symbol,
)


class TestFindColumn:
    def test_exact_match(self):
        assert find_column(["Symbol", "Description"], "symbol") == 0

    def test_alternate_name_match(self):
        # COLUMN_MAPPINGS['symbol'] includes 'Security'
        assert find_column(["Security", "Description"], "symbol") == 0

    def test_whitespace_and_case_insensitive(self):
        assert find_column(["  date  acquired  ", "X"], "acquired_date") == 0

    def test_no_match_returns_none(self):
        assert find_column(["Foo", "Bar"], "symbol") is None


class TestParseMoney:
    def test_plain_dollar_amount(self):
        assert parse_money("$1,234.56") == 1234.56

    def test_parenthesized_negative(self):
        assert parse_money("(123.45)") == -123.45

    def test_leading_minus_with_dollar(self):
        assert parse_money("-$1,234.56") == -1234.56

    def test_european_format(self):
        assert parse_money("1.234,56") == 1234.56

    def test_empty_or_dash_is_zero(self):
        assert parse_money("") == 0.0
        assert parse_money("-") == 0.0
        assert parse_money("--") == 0.0

    def test_currency_code_prefix(self):
        assert parse_money("USD 1,234.56") == 1234.56


class TestParseDate:
    def test_us_slash_format(self):
        assert parse_date("01/17/2025") == "2025-01-17"

    def test_iso_format(self):
        assert parse_date("2025-01-17") == "2025-01-17"

    def test_two_digit_year(self):
        assert parse_date("01/17/25") == "2025-01-17"

    def test_empty_or_dash_returns_none(self):
        assert parse_date("") is None
        assert parse_date("-") is None

    def test_unparseable_returns_none(self):
        assert parse_date("not a date") is None


class TestParseOptionDescription:
    def test_standard_fidelity_put(self):
        result = parse_option_description("PUT (NVDA) NVIDIA CORP JAN 17 25 $150.00")
        assert result["option_type"] == "PUT"
        assert result["underlying"] == "NVDA"
        assert result["strike"] == 150.00
        assert result["expiration"] == "2025-01-17"

    def test_standard_fidelity_call_long_year(self):
        result = parse_option_description("CALL (AAPL) APPLE INC FEB 21 2025 $225")
        assert result["option_type"] == "CALL"
        assert result["underlying"] == "AAPL"
        assert result["strike"] == 225.0
        assert result["expiration"] == "2025-02-21"

    def test_compact_format(self):
        result = parse_option_description("NVDA 02/07/2026 150.00 C")
        assert result["option_type"] == "CALL"
        assert result["underlying"] == "NVDA"
        assert result["strike"] == 150.00
        assert result["expiration"] == "2026-02-07"

    def test_not_an_option_returns_all_none(self):
        result = parse_option_description("APPLE INC COMMON STOCK")
        assert result["option_type"] is None
        assert result["underlying"] is None

    def test_empty_description(self):
        result = parse_option_description("")
        assert result["option_type"] is None


class TestParseOccSymbol:
    def test_standard_occ_format(self):
        result = parse_occ_symbol("AAPL250117C00150000")
        assert result["underlying"] == "AAPL"
        assert result["option_type"] == "CALL"
        assert result["expiration"] == "2025-01-17"
        assert result["strike"] == 150.0

    def test_put_with_cusip_suffix(self):
        result = parse_occ_symbol("ACN250926P215(8061839XV)")
        assert result["underlying"] == "ACN"
        assert result["option_type"] == "PUT"
        assert result["expiration"] == "2025-09-26"

    def test_empty_symbol(self):
        result = parse_occ_symbol("")
        assert result["underlying"] is None
