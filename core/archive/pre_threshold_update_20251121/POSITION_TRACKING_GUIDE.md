# Position Tracking & Decision Support Guide

**New Features for Manual Trading on Fidelity**

This guide covers the new position tracking and decision support tools that help you:
1. **See portfolio impact BEFORE executing** on Fidelity
2. **Track open positions** and monitor stop losses
3. **Learn from your trades** with performance analytics

---

## Quick Start

### 1. Initialize Position Tracking (One-Time Setup)

```bash
cd /home/user/trading-desk/2.0
source venv/bin/activate
python scripts/init_positions.py
```

This adds position tracking tables to your existing database.

---

## Features Overview

### 📊 Pre-Trade Risk Analysis

**What it does:** Shows portfolio impact BEFORE you execute on Fidelity

**When to use:** Every time you get a "TRADEABLE" recommendation

**What you see:**
- Portfolio exposure if you take this trade
- Sector concentration warnings
- Correlation with existing positions
- Stress test scenarios (what if it goes wrong?)
- Historical context (how did similar trades perform?)
- **PROCEED / CAUTION / REJECT** recommendation

**Example:**
```bash
./trade.sh NVDA 2025-11-20 --strategies
```

You'll see:
```
PRE-TRADE RISK ANALYSIS

PROPOSED TRADE: NVDA
Position Size:         5.0% of account ($2,000 max loss)
VRP Ratio:             2.17x

PORTFOLIO IMPACT
Current Exposure:      8.5%
New Total Exposure:    13.5% (+5.0%)

SECTOR ANALYSIS (Technology)
Current Sector Exposure: 8.5%
New Sector Exposure:     13.5% (+5.0%)

CORRELATION ANALYSIS
Correlated Positions:  TSLA, AMD
⚠️  High correlation with existing positions

STRESS SCENARIOS
  Base Case             $1,200  (High 70-80%)
  Breakeven Move        $0      (Low 10-15%)
  Max Loss              -$2,000 (Low 5-10%)

HISTORICAL CONTEXT
Similar Trades:        3
Historical Win Rate:   100%
Avg P&L:               $1,350

✓ RECOMMENDATION: PROCEED
  ✓ Strong VRP ratio (2.17x)
  ✓ Strong historical win rate (100%)
  ✓ Portfolio exposure well within limits (13.5%)
```

**Use this to decide:** Should I execute this trade on Fidelity right now?

---

### 📱 Position Dashboard

**What it does:** Shows all your open positions at a glance

**When to use:** Daily monitoring, before adding new positions

**Command:**
```bash
./trade.sh positions
```

**What you see:**
```
CURRENT POSITIONS

Ticker   Entry      Exp        Days  Credit     P&L            Status
NVDA     11/18      11/21      2     $1,500     +$750 (50%)    ✓ Target
WMT      11/17      11/20      3     $800       -$400 (50%)    ⚠ Stop Loss
AMD      11/16      11/19      4     $1,200     +$900 (75%)    ✓ Close

PORTFOLIO SUMMARY
Total Positions:       3
Portfolio Exposure:    13.5% of account
Capital at Risk:       $3,500
Unrealized P&L:        +$1,250
Avg VRP Ratio:         2.05x
Avg Days Held:         3.0

⚠️  STOP LOSS ALERTS: WMT
✓ AT TARGET PROFIT: NVDA, AMD

SECTOR EXPOSURE
  Technology          13.5%
  Consumer            5.0%
```

**Use this to:**
- Monitor current positions
- See stop loss alerts
- Check portfolio exposure before adding new trades

---

### 📈 Performance Analytics

**What it does:** Learning loops - shows what's working and what's not

**When to use:** Weekly review, parameter optimization

**Command:**
```bash
./trade.sh performance
```

**What you see:**
```
PERFORMANCE ANALYTICS

OVERALL PERFORMANCE
Total Closed Trades:   28
Win Rate:              82.1%
Total P&L:             +$24,350

PERFORMANCE BY VRP RATIO
Bucket          Trades   Win Rate   Avg P&L    Total P&L
Very High       8        100.0%     $1,850     $14,800
High            12       91.7%      $1,200     $14,400
Good            8        62.5%      $800       $6,400
Marginal        0        -          -          -

PARAMETER INSIGHTS

VRP Threshold: Very High
  Win Rate:      100% (8 trades)
  Avg P&L:       $1,850
  Recommendation: MAINTAIN
  VRP >2.5 trades have 100% win rate. Current threshold is optimal.

TOP PERFORMERS
  1. NVDA (100% WR, $8,450)
  2. WMT (100% WR, $4,200)
  3. AMD (87.5% WR, $3,800)

BOTTOM PERFORMERS
  1. TSLA (50% WR, -$1,200)
  2. META (66% WR, $400)

RECOMMENDATIONS
  📊 VRP >2.0 trades have 100% win rate. Raise threshold to 2.0 minimum.
  📈 STRADDLE strategy has 85% win rate. Consider allocating more.
  ⚠️ Consider blacklisting poor performers: TSLA
```

**Use this to:**
- Identify which VRP thresholds work best for YOU
- Find your best/worst tickers
- Optimize your strategy mix
- Adjust parameters based on actual results

---

## Workflow: Taking a Trade

### Step 1: Analyze Opportunity

```bash
./trade.sh NVDA 2025-11-20
```

### Step 2: Review Pre-Trade Risk

The output will show:
- **TRADEABLE or SKIP** recommendation
- **Pre-Trade Risk Analysis** (if tradeable)
- **Strategy recommendations**

### Step 3: Decide

Based on pre-trade risk analysis:
- **PROCEED** = Go ahead, execute on Fidelity
- **CAUTION** = Consider reducing size or managing existing positions first
- **REJECT** = Skip this trade

### Step 4: Execute on Fidelity (Manual)

If you decide to proceed:
1. Log into Fidelity
2. Enter the recommended strategy
3. Confirm fills

### Step 5: Add to Position Tracker

```bash
python scripts/add_position.py NVDA 2025-11-18 2025-11-20 2025-11-21 \
    --credit 1500 \
    --max-loss 2000 \
    --vrp 2.17 \
    --implied-move 8.0 \
    --historical-move 3.69 \
    --strategy STRADDLE \
    --position-size 5.0 \
    --notes "Strong VRP, high conviction"
```

Arguments:
- `NVDA` - Ticker
- `2025-11-18` - Entry date (today)
- `2025-11-20` - Earnings date
- `2025-11-21` - Expiration date
- `--credit` - Credit received (dollars)
- `--max-loss` - Maximum loss (dollars)
- `--vrp` - VRP ratio from analysis
- `--implied-move` - Implied move % from analysis
- `--historical-move` - Historical avg move % from analysis
- `--strategy` - Strategy type (STRADDLE, IRON_CONDOR, BULL_PUT_SPREAD, etc.)
- `--position-size` - Position size % of account
- `--notes` - Optional entry notes

### Step 6: Monitor Daily

```bash
./trade.sh positions
```

Check for:
- Stop loss alerts
- Target profit alerts
- Days until expiration

### Step 7: Close Position on Fidelity

When you exit the trade on Fidelity (usually morning after earnings):
1. Note the closing stock price
2. Note your actual P&L
3. Calculate actual stock move %

### Step 8: Record Outcome

```bash
python scripts/close_position.py --ticker NVDA \
    --close-price 188.50 \
    --actual-move 3.2 \
    --pnl 1400 \
    --notes "IV crushed as expected, closed at 9:32 AM"
```

Arguments:
- `--ticker` - Stock ticker (or use `--id` for position ID)
- `--close-price` - Closing stock price
- `--actual-move` - Actual stock move percentage
- `--pnl` - Final P&L in dollars
- `--notes` - Optional exit notes

You'll see:
```
✓ Position closed successfully!
  Ticker:       NVDA
  Entry:        2025-11-18
  Close:        2025-11-21
  Days Held:    3

  THESIS:
  VRP Ratio:    2.17x
  Implied Move: 8.0%
  Historical:   3.69%

  OUTCOME:
  Actual Move:  3.2%
  Result:       WIN
  P&L:          $1,400 (+93%)

  ✓ Trade worked as expected!
```

### Step 9: Learn

Review performance weekly:

```bash
./trade.sh performance
```

Use insights to refine your approach.

---

## Example: Full Trade Lifecycle

**Monday, Nov 18, 2025 - 3:00 PM**

1. **Analyze opportunity:**
```bash
./trade.sh NVDA 2025-11-20
```

2. **See pre-trade risk:**
```
PRE-TRADE RISK ANALYSIS
...
✓ RECOMMENDATION: PROCEED
```

3. **Execute on Fidelity:**
- Sell NVDA Nov 21 $188 straddle for $15.04 credit

4. **Add to tracker:**
```bash
python scripts/add_position.py NVDA 2025-11-18 2025-11-20 2025-11-21 \
    --credit 1504 --max-loss 2000 --vrp 2.17 --implied-move 8.0 \
    --historical-move 3.69 --strategy STRADDLE --sector Technology
```

**Tuesday, Nov 19, 2025 - 8:00 AM**

5. **Monitor positions:**
```bash
./trade.sh positions
```
```
Ticker   Entry      Exp        Days  Credit     P&L            Status
NVDA     11/18      11/21      1     $1,504     $0 (0%)        Monitoring
```

**Wednesday, Nov 20, 2025 - 9:30 AM (After Earnings)**

6. **Check on Fidelity:**
- NVDA moved 3.2% (less than 8% breakeven)
- Straddle worth $0.80 (was $15.04)
- Close for $1,424 profit ($15.04 - $0.80)

7. **Close position in tracker:**
```bash
python scripts/close_position.py --ticker NVDA \
    --close-price 188.50 --actual-move 3.2 --pnl 1424
```

**Friday, Nov 22, 2025**

8. **Weekly review:**
```bash
./trade.sh performance
```

---

## Command Reference

### Position Management

**View positions:**
```bash
./trade.sh positions
```

**Add position after manual entry on Fidelity:**
```bash
python scripts/add_position.py TICKER ENTRY_DATE EARNINGS_DATE EXP_DATE \
    --credit AMOUNT --max-loss AMOUNT --vrp RATIO \
    --implied-move PCT --historical-move PCT
```

**Close position after manual exit on Fidelity:**
```bash
python scripts/close_position.py --ticker TICKER \
    --close-price PRICE --actual-move PCT --pnl AMOUNT
```

Or by position ID:
```bash
python scripts/close_position.py --id 123 \
    --close-price PRICE --actual-move PCT --pnl AMOUNT
```

### Analytics

**View performance:**
```bash
./trade.sh performance
```

**View last 90 days only:**
```bash
python scripts/performance.py --days 90
```

---

## Tips

### Pre-Trade Risk

- **PROCEED** - All systems go, no major concerns
- **CAUTION** - Acceptable but watch exposure/correlation
- **REJECT** - Skip or reduce existing positions first

### Position Tracking

- Add position IMMEDIATELY after executing on Fidelity
- Update daily if you want to track intraday P&L (optional)
- Close position same day you exit on Fidelity

### Performance Analytics

- Review weekly or monthly
- Use insights to adjust VRP thresholds
- Blacklist consistently poor performers
- Focus on your top performers

### Risk Management

- **Max 20% portfolio exposure** - System will warn you
- **Max 40% sector concentration** - System will warn you
- **Set stop losses** - System will alert you
- **Target 50% profit** - System will notify you

---

## Troubleshooting

### "Position already exists"
You already added this position. Use `./trade.sh positions` to see it.

### "Position not found"
Check ticker spelling or use `./trade.sh positions` to see open positions.

### "No closed trades yet"
Performance analytics requires closed positions. Close some trades first.

### Multiple positions for same ticker
When closing, you'll be prompted to specify `--id` instead of `--ticker`.

---

## Advanced Usage

### Sector Tracking

Add sector information for better analytics:
```bash
python scripts/add_position.py ... --sector Technology
```

### Entry Notes

Document your thesis:
```bash
python scripts/add_position.py ... --notes "Strong VRP, earnings beat expected"
```

### Exit Notes

Document what happened:
```bash
python scripts/close_position.py ... --notes "Closed early at 50% profit, IV crush faster than expected"
```

### Stop Loss Alerts

Set a stop loss amount:
```bash
python scripts/add_position.py ... --stop-loss 1600
```

System will alert you in `./trade.sh positions` if current P&L hits stop loss.

### Target Profit

Set a target profit:
```bash
python scripts/add_position.py ... --target-profit 800
```

System will notify you in `./trade.sh positions` when target is reached.

---

## Summary

**Before taking trade:**
1. Run `./trade.sh TICKER DATE` to analyze
2. Review pre-trade risk analysis
3. Check `./trade.sh positions` for current exposure

**After executing on Fidelity:**
1. Run `python scripts/add_position.py` to track
2. Monitor daily with `./trade.sh positions`

**After closing on Fidelity:**
1. Run `python scripts/close_position.py` to record outcome
2. Review weekly with `./trade.sh performance`

**The system helps you make better decisions, but YOU execute on Fidelity.**

---

**Questions? Check the main README.md or LIVE_TRADING_GUIDE.md**
