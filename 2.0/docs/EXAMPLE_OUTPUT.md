# Example Scanner Outputs

This document shows example outputs from the different scanning modes with metric explanations.

---

## Whisper Mode Output (Updated)

```bash
./trade.sh whisper
```

### Output:
```
═══════════════════════════════════════
    Most Anticipated Earnings
═══════════════════════════════════════

Week: 2025-11-17 to 2025-11-23
Fetching ticker list...
✓ Retrieved 15 most anticipated tickers
Tickers: NVDA, SNOW, PANW, ANET, CRM, TGT, LOW, WMT, TJX, ADSK, DE, ZBRA, HPQ, INTC, DELL

Analyzing tickers:  33%|███▎      | 5/15 [00:15<00:30] , NVDA: ✓ Complete

================================================================================
WHISPER MODE - SUMMARY
================================================================================

🔊 Most Anticipated Earnings Analysis:
   Mode: Earnings Whispers (Reddit r/EarningsWhispers)
   Week: 2025-11-17 to 2025-11-23
   Total Tickers: 15

📊 Analysis Results:
   🔍 Filtered (Market Cap/Liquidity): 3
   ✓ Successfully Analyzed: 9
   ⏭️  Skipped (No Earnings/Data): 2
   ✗ Errors: 1

================================================================================
✅ RESULT: 6 TRADEABLE OPPORTUNITIES FOUND
================================================================================

🎯 Most Anticipated + High VRP (Ranked by VRP Ratio):
   1. NVDA  : VRP 8.21x | Implied 7.5% | Edge 5.12 | EXCELLENT | Earnings 2025-11-20
   2. SNOW  : VRP 7.84x | Implied 12.8% | Edge 4.89 | EXCELLENT | Earnings 2025-11-20
   3. PANW  : VRP 6.94x | Implied 8.2% | Edge 4.33 | EXCELLENT | Earnings 2025-11-21
   4. CRM   : VRP 5.47x | Implied 6.3% | Edge 3.82 | EXCELLENT | Earnings 2025-11-21
   5. ANET  : VRP 4.15x | Implied 9.1% | Edge 2.94 | EXCELLENT | Earnings 2025-11-19
   6. TGT   : VRP 3.28x | Implied 5.5% | Edge 2.11 | GOOD     | Earnings 2025-11-19

💡 Why This Matters:
   These tickers combine:
   • High retail/market attention (Most Anticipated)
   • Strong statistical edge (VRP ratio)
   • Better liquidity expected (High volume)

📝 Understanding the Metrics:
   • VRP Ratio = Implied Move ÷ Historical Move (Higher = Better Edge)
   • Implied Move = Market's expectation (from options prices)
   • Edge Score = Statistical confidence (Higher = More reliable)
   • EXCELLENT (>4.0x), GOOD (2.5-4.0x), MARGINAL (1.5-2.5x), SKIP (<1.5x)

📝 Next Steps:
   1. Analyze top opportunities with: ./trade.sh TICKER YYYY-MM-DD
   2. Review detailed strategy recommendations
   3. Prioritize by VRP ratio and market attention
   4. Check broker for tight bid-ask spreads
   5. For detailed metrics guide: cat docs/METRICS_GUIDE.md

✓ Complete
```

---

## Scan Mode Output

```bash
./trade.sh scan 2025-11-20
```

### Output:
```
═══════════════════════════════════════
    Scanning Earnings for 2025-11-20
═══════════════════════════════════════

Scanning earnings:  67%|██████▋   | 12/18 [01:45<00:52] , NVDA: ✓ Complete

================================================================================
SCAN MODE - SUMMARY
================================================================================

📅 Scan Details:
   Mode: Earnings Date Scan
   Date: 2025-11-20
   Total Earnings Found: 18

📊 Analysis Results:
   🔍 Filtered (Market Cap/Liquidity): 6
   ✓ Successfully Analyzed: 8
   ⏭️  Skipped (No Data): 3
   ✗ Errors: 1

================================================================================
✅ RESULT: 4 TRADEABLE OPPORTUNITIES FOUND
================================================================================

🎯 Ranked by VRP Ratio:
   1. NVDA  : VRP 8.21x | Implied 7.5% | Edge 5.12 | EXCELLENT
   2. SNOW  : VRP 7.84x | Implied 12.8% | Edge 4.89 | EXCELLENT
   3. ADBE  : VRP 5.12x | Implied 10.3% | Edge 3.42 | EXCELLENT
   4. ZS    : VRP 3.47x | Implied 8.9% | Edge 2.28 | GOOD

📝 Understanding the Metrics:
   • VRP Ratio = Implied Move ÷ Historical Move (Higher = Better Edge)
   • Implied Move = Market's expectation (from options prices)
   • Edge Score = Statistical confidence (Higher = More reliable)
   • EXCELLENT (>4.0x), GOOD (2.5-4.0x), MARGINAL (1.5-2.5x), SKIP (<1.5x)

📝 Next Steps:
   1. Analyze individual tickers with: ./trade.sh TICKER 2025-11-20
   2. Review strategy recommendations for each opportunity
   3. Check broker pricing before entering positions
   4. For detailed metrics guide: cat docs/METRICS_GUIDE.md

✓ Complete
```

---

## List Mode Output

```bash
./trade.sh list NVDA,SNOW,PANW,CRM 2025-11-20
```

### Output:
```
═══════════════════════════════════════
    Analyzing Multiple Tickers
═══════════════════════════════════════
Tickers: NVDA,SNOW,PANW,CRM
Earnings Date: 2025-11-20
Expiration Offset: +1 days

Analyzing tickers: 100%|██████████| 4/4 [01:12<00:00] , CRM: ✓ Complete

================================================================================
LIST MODE - SUMMARY
================================================================================

📋 Ticker List Analysis:
   Mode: Multiple Ticker Analysis
   Tickers Requested: 4
   Tickers Analyzed: NVDA, SNOW, PANW, CRM

📊 Analysis Results:
   🔍 Filtered (Market Cap/Liquidity): 0
   ✓ Successfully Analyzed: 4
   ⏭️  Skipped (No Earnings/Data): 0
   ✗ Errors: 0

================================================================================
✅ RESULT: 4 TRADEABLE OPPORTUNITIES FOUND
================================================================================

🎯 Ranked by VRP Ratio:
   1. NVDA  : VRP 8.21x | Implied 7.5% | Edge 5.12 | EXCELLENT | Earnings 2025-11-20
   2. SNOW  : VRP 7.84x | Implied 12.8% | Edge 4.89 | EXCELLENT | Earnings 2025-11-20
   3. PANW  : VRP 6.94x | Implied 8.2% | Edge 4.33 | EXCELLENT | Earnings 2025-11-21
   4. CRM   : VRP 5.47x | Implied 6.3% | Edge 3.82 | EXCELLENT | Earnings 2025-11-21

📝 Understanding the Metrics:
   • VRP Ratio = Implied Move ÷ Historical Move (Higher = Better Edge)
   • Implied Move = Market's expectation (from options prices)
   • Edge Score = Statistical confidence (Higher = More reliable)
   • EXCELLENT (>4.0x), GOOD (2.5-4.0x), MARGINAL (1.5-2.5x), SKIP (<1.5x)

📝 Next Steps:
   1. Analyze top opportunities with: ./trade.sh TICKER YYYY-MM-DD
   2. Review detailed strategy recommendations
   3. Prioritize by VRP ratio and edge score
   4. Verify earnings dates and check broker pricing
   5. For detailed metrics guide: cat docs/METRICS_GUIDE.md

✓ Complete
```

---

## Individual Ticker Analysis

```bash
./trade.sh NVDA 2025-11-20
```

### Output:
```
═══════════════════════════════════════
    Analyzing NVDA for 2025-11-20
═══════════════════════════════════════

================================================================================
Analyzing NVDA
================================================================================
Earnings Date: 2025-11-20
Expiration: 2025-11-21

📊 Calculating Implied Move...
✓ Implied Move: 7.50%
  Stock Price: $145.23
  ATM Strike: $145.00
  Straddle Cost: $10.89

📊 Fetching Historical Moves...
✓ Found 12 historical moves

📊 Calculating VRP...
✓ VRP Ratio: 8.21x
  Implied Move: 7.50%
  Historical Mean: 0.91%
  Edge Score: 5.12
  Recommendation: EXCELLENT

✅ TRADEABLE OPPORTUNITY

================================================================================
STRATEGY RECOMMENDATIONS
================================================================================

★ RECOMMENDED: IRON CONDOR
  Short Strikes: $140P / $150C
  Long Strikes: $135P / $155C
  Net Credit: $2.15
  Max Profit: $15,050 (70 contracts)
  Probability of Profit: 68.4%
  Reward/Risk: 0.73
  Theta: +$512/day

[... additional strategy details ...]

✓ Complete
```

---

## Metric Explanation Example

### Example 1: High VRP

**Ticker: NVDA**
```
VRP 8.21x | Implied 7.5% | Edge 5.12 | EXCELLENT
```

**What this means:**
- **VRP 8.21x**: Market overprices options by 8.21 times vs. reality
- **Implied 7.5%**: Market expects ±7.5% move after earnings
- **Edge 5.12**: Very high confidence in this signal
- **EXCELLENT**: Strong trade recommendation (VRP > 4.0x)

**Why it's good:**
- Market prices options for 7.5% move
- Stock historically moves only 0.91% (8.21x less!)
- You sell expensive premium, keep it when stock moves less than expected

---

### Example 2: Moderate VRP

**Ticker: TGT**
```
VRP 3.28x | Implied 5.5% | Edge 2.11 | GOOD
```

**What this means:**
- **VRP 3.28x**: Moderate overpricing (3x reality)
- **Implied 5.5%**: Market expects smaller move
- **Edge 2.11**: Moderate confidence
- **GOOD**: Tradeable but lower priority (VRP 2.5-4.0x)

**Trade-off:**
- Lower premium collected (5.5% implied vs NVDA's 7.5%)
- Lower VRP = smaller edge
- Still profitable, but rank below EXCELLENT trades

---

## Quick Decision Matrix

```
High VRP + High Implied Move = BEST TRADES
│
├─ NVDA: VRP 8.21x, Implied 7.5%  → Trade first (max premium + edge)
├─ SNOW: VRP 7.84x, Implied 12.8% → Trade second (highest premium)
├─ PANW: VRP 6.94x, Implied 8.2%  → Trade third (strong edge)
└─ CRM:  VRP 5.47x, Implied 6.3%  → Trade fourth (good edge)

Moderate VRP
│
└─ TGT:  VRP 3.28x, Implied 5.5%  → Trade if capital available
```

---

## For More Details

- **Comprehensive Guide**: `cat docs/METRICS_GUIDE.md`
- **Trading Operations**: `cat LIVE_TRADING_GUIDE.md`
- **Position Sizing**: `cat POSITION_SIZING_DEPLOYMENT.md`
- **System Overview**: `cat README.md`
