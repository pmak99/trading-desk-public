# Changelog

All notable changes to IV Crush 2.0 will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Fixed spread width configuration: `SPREAD_WIDTH_HIGH_PRICE` (default $5), `SPREAD_WIDTH_LOW_PRICE` (default $3), `SPREAD_WIDTH_THRESHOLD` (default $20)
- Deprecation warnings for `TARGET_DELTA_LONG` and `SPREAD_WIDTH_PERCENT` environment variables
- Package order optimization: Liquidity scoring now only evaluates SHORT legs for spreads/condors

### Changed
- **BREAKING**: `target_delta_short` changed from 0.30 (30-delta, 70% POP) to 0.25 (25-delta, 75% POP)
- **BREAKING**: `min_credit_per_spread` lowered from $0.25 to $0.20 to accommodate 25-delta strategies
- **BREAKING**: Spread width selection changed from percentage-based (3% of stock price) to fixed dollar amounts ($5 for stocks ≥$20, $3 for stocks <$20)
- **BREAKING**: Expiration selection for Thursday/Friday earnings changed from same-week Friday to 1-week-out Friday
- Liquidity scoring for strategies now only checks SHORT legs (where premium is collected), not protection legs

### Deprecated
- `TARGET_DELTA_LONG` - Long strike selection now uses fixed dollar spread widths instead of delta targeting
- `SPREAD_WIDTH_PERCENT` - Replaced by fixed dollar amounts (`SPREAD_WIDTH_HIGH_PRICE`, `SPREAD_WIDTH_LOW_PRICE`)

### Fixed
- Expiration calculation for Thursday AMC earnings (was using same-week Friday, now correctly uses next-week Friday)
- Expiration calculation for Friday BMO earnings (was using 0DTE same day, now correctly uses next-week Friday)

## Migration Guide

### Configuration Changes

If you were using custom environment variables, update them as follows:

#### Strike Selection
```bash
# Old (deprecated)
export TARGET_DELTA_SHORT=0.30    # 30-delta short strikes
export TARGET_DELTA_LONG=0.20     # 20-delta long strikes (IGNORED)

# New (recommended)
export TARGET_DELTA_SHORT=0.25    # 25-delta short strikes (balanced)
# No need to set TARGET_DELTA_LONG - spread width is now fixed dollar amounts
```

#### Spread Width
```bash
# Old (deprecated)
export SPREAD_WIDTH_PERCENT=0.03  # 3% of stock price (IGNORED)

# New (recommended)
export SPREAD_WIDTH_HIGH_PRICE=5.0   # $5 spread for stocks >= $20
export SPREAD_WIDTH_LOW_PRICE=3.0    # $3 spread for stocks < $20
export SPREAD_WIDTH_THRESHOLD=20.0   # Threshold price
```

#### Credit Threshold
```bash
# Old
export MIN_CREDIT_PER_SPREAD=0.25

# New (if you want to use 25-delta strategies)
export MIN_CREDIT_PER_SPREAD=0.20

# Or keep $0.25 if you prefer higher premiums (may filter out some 25-delta trades)
```

### Behavioral Changes

#### 1. Expiration Selection
- **Monday/Tuesday/Wednesday earnings**: No change (Friday of same week)
- **Thursday AMC earnings**: Now uses Friday 1 week out (was same-week Friday)
- **Friday BMO/AMC earnings**: Now uses Friday 1 week out (was same day for BMO)

This aligns with the strategy of avoiding 0DTE trades and using Friday weekly expirations.

#### 2. Spread Width
- **Stock at $50**: Old = $1.50 (3%), New = $5.00 (fixed)
- **Stock at $250 (SNOW)**: Old = $7.50 (3%), New = $5.00 (fixed)
- **Stock at $15**: Old = $5.00 (max of 3% or $5), New = $3.00 (fixed)

Fixed spreads provide more predictable max loss per contract ($500 or $300).

#### 3. Probability of Profit
- **Old (30-delta)**: ~70% POP
- **New (25-delta)**: ~75-82% POP (higher win rate)

Trade-off: Slightly less premium collected, but higher probability of success.

#### 4. Liquidity Scoring
For package orders (spreads/condors), only SHORT legs are evaluated for liquidity:
- **Bear Call Spread**: Only checks short call leg
- **Bull Put Spread**: Only checks short put leg
- **Iron Condor**: Checks both short strikes (put + call)
- **Long (protection) legs**: No longer impact liquidity tier

This results in more opportunities passing liquidity filters for package-traded strategies.

### Reverting to Old Behavior

If you prefer the previous configuration:

```bash
# Revert to 30-delta, percentage-based spreads
export TARGET_DELTA_SHORT=0.30
export MIN_CREDIT_PER_SPREAD=0.25
# Note: Spread width and expiration logic cannot be reverted via env vars
# You would need to use a previous version or modify source code
```

### Testing Your Configuration

Test with a known ticker to verify behavior:

```bash
# Test with SNOW
./trade.sh SNOW 2025-12-03

# Expected results with new config (25-delta, $5 spreads):
# - Bear Call: ~$280C/$285C ($5 spread), 79%+ POP
# - Bull Put: ~$222P/$217P ($5 spread), 82%+ POP
# - Credit: $0.78-$0.88 per spread

# Compare with your expected results
```

## [2.0.0] - 2025-11-30

### Major Release
- Complete rewrite of IV Crush system
- Modular architecture with dependency injection
- Enhanced liquidity scoring (3-tier system)
- Improved VRP calculations
- Package order support

---

For questions or issues with migration, please open a GitHub issue.
