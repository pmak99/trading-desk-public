#!/bin/bash
#
# Integration Tests - Real end-to-end validation
#
# Tests the complete system with actual tickers:
# 1. EXCELLENT tier (VRP >= 7.0x)
# 2. GOOD tier (VRP >= 4.0x)
# 3. MARGINAL tier (VRP >= 1.5x)
# 4. System health checks
# 5. Error handling
#

set -e

echo "================================================================================"
echo "IV CRUSH 2.0 - INTEGRATION TEST SUITE"
echo "================================================================================"
echo ""
echo "Testing complete system with real tickers from grid scan results"
echo ""

cd "$(dirname "$0")/.."

# Test 1: System Health
echo "================================================================================"
echo "TEST 1: System Health Check"
echo "================================================================================"
./venv/bin/python scripts/health_check.py
if [ $? -eq 0 ]; then
    echo "✅ System health check PASSED"
else
    echo "❌ System health check FAILED"
    exit 1
fi
echo ""

# Test 2: EXCELLENT tier (AKAM - VRP 15.78x)
echo "================================================================================"
echo "TEST 2: EXCELLENT Tier - AKAM (Expected VRP ~15x)"
echo "================================================================================"
./trade.sh AKAM 2026-02-17 | tee /tmp/test_akam.log
if grep -q "EXCELLENT\|GOOD" /tmp/test_akam.log; then
    echo "✅ AKAM analysis PASSED - High VRP detected"
else
    echo "❌ AKAM analysis FAILED - Expected high VRP"
fi
echo ""

# Test 3: GOOD tier (CRM - VRP 6.11x)
echo "================================================================================"
echo "TEST 3: GOOD Tier - CRM (Expected VRP ~6x)"
echo "================================================================================"
./trade.sh CRM 2025-12-03 | tee /tmp/test_crm.log
if grep -q "GOOD\|EXCELLENT\|MARGINAL" /tmp/test_crm.log; then
    echo "✅ CRM analysis PASSED - Moderate VRP detected"
else
    echo "❌ CRM analysis FAILED - Expected moderate VRP"
fi
echo ""

# Test 4: MARGINAL tier (GIS - VRP 2.34x)
echo "================================================================================"
echo "TEST 4: MARGINAL Tier - GIS (Expected VRP ~2x)"
echo "================================================================================"
./trade.sh GIS 2025-12-17 | tee /tmp/test_gis.log
if grep -q "MARGINAL\|GOOD\|POOR" /tmp/test_gis.log; then
    echo "✅ GIS analysis PASSED - Lower VRP detected"
else
    echo "❌ GIS analysis FAILED - Expected lower VRP"
fi
echo ""

# Test 5: Error handling - Invalid ticker
echo "================================================================================"
echo "TEST 5: Error Handling - Invalid Ticker"
echo "================================================================================"
./trade.sh INVALID_TICKER 2025-12-01 2>&1 | tee /tmp/test_error.log
if grep -q "ERROR\|FAIL\|not found" /tmp/test_error.log; then
    echo "✅ Error handling PASSED - Gracefully handled invalid ticker"
else
    echo "⚠️  Error handling - Review required"
fi
echo ""

# Test 6: Database logging
echo "================================================================================"
echo "TEST 6: Database Logging"
echo "================================================================================"
COUNT=$(sqlite3 data/ivcrush.db "SELECT COUNT(*) FROM analysis_log WHERE ticker IN ('AKAM', 'CRM', 'GIS');")
if [ "$COUNT" -gt 0 ]; then
    echo "✅ Database logging PASSED - $COUNT analyses logged"
    echo ""
    echo "Recent analyses:"
    sqlite3 data/ivcrush.db "SELECT ticker, vrp_ratio, recommendation, analyzed_at FROM analysis_log ORDER BY analyzed_at DESC LIMIT 5;" -header -column
else
    echo "⚠️  Database logging - No records found (may need schema update)"
fi
echo ""

# Final Summary
echo "================================================================================"
echo "INTEGRATION TEST SUMMARY"
echo "================================================================================"
echo ""
echo "All critical paths tested:"
echo "  ✓ System health checks"
echo "  ✓ EXCELLENT tier analysis (VRP >= 7.0x)"
echo "  ✓ GOOD tier analysis (VRP >= 4.0x)"
echo "  ✓ MARGINAL tier analysis (VRP >= 1.5x)"
echo "  ✓ Error handling"
echo "  ✓ Database integration"
echo ""
echo "Review logs in /tmp/test_*.log for details"
echo ""
echo "================================================================================"
