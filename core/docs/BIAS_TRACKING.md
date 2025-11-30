# Directional Bias Prediction Tracking

This document explains the directional bias prediction and validation system for testing the accuracy of our 7-level bias detection.

## Overview

The bias tracking system allows us to:
1. **Store predictions** before earnings announcements
2. **Validate predictions** against actual price moves after earnings
3. **Generate accuracy reports** to measure system performance
4. **Improve the model** based on validated results

## Database Schema

### `bias_predictions` Table

Stores directional bias predictions before earnings:

| Column | Type | Description |
|--------|------|-------------|
| ticker | TEXT | Stock symbol |
| earnings_date | DATE | Earnings announcement date |
| expiration | DATE | Option expiration used for analysis |
| stock_price | REAL | Stock price when prediction made |
| predicted_at | DATETIME | Timestamp of prediction |
| skew_atm | REAL | IV skew at ATM (%) |
| skew_curvature | REAL | Second derivative (smile measure) |
| skew_strength | TEXT | smile, smirk, inverse_smile |
| slope_atm | REAL | First derivative at ATM |
| directional_bias | TEXT | Predicted bias (e.g., "strong_bearish") |
| bias_strength | INTEGER | 0=NEUTRAL, 1=WEAK, 2=MODERATE, 3=STRONG |
| bias_confidence | REAL | Confidence 0.0-1.0 |
| r_squared | REAL | Polynomial fit quality |
| num_points | INTEGER | Data points used in fit |
| vrp_ratio | REAL | VRP context (optional) |
| implied_move_pct | REAL | Implied move % (optional) |
| historical_mean_pct | REAL | Historical avg move (optional) |
| actual_move_pct | REAL | Actual close-to-close move |
| actual_gap_pct | REAL | Actual gap move |
| actual_direction | TEXT | UP, DOWN, FLAT |
| prediction_correct | BOOLEAN | Did bias match direction? |
| validated_at | DATETIME | When validation occurred |

### `bias_accuracy_stats` Table

Stores periodic snapshots of accuracy statistics for trend analysis.

## Workflow

### 1. Store Predictions (Before Earnings)

**Script**: `scripts/store_bias_prediction.py`

```bash
# Store prediction for specific tickers
python scripts/store_bias_prediction.py CRM TSLA AAPL

# Store for all upcoming earnings (next 14 days)
python scripts/store_bias_prediction.py --all

# Custom date range
python scripts/store_bias_prediction.py --start 2025-12-01 --end 2025-12-07
```

**What it does**:
- Analyzes IV skew using polynomial fitting
- Calculates directional bias (7-level scale)
- Stores prediction in `bias_predictions` table
- Captures VRP context if available

**Example Output**:
```
CRM: ✓ Stored prediction - strong_bearish (confidence=0.36)
```

### 2. Validate Predictions (After Earnings)

**Script**: `scripts/validate_bias_predictions.py`

```bash
# Validate all unvalidated predictions
python scripts/validate_bias_predictions.py

# Validate specific ticker
python scripts/validate_bias_predictions.py CRM

# Validate date range
python scripts/validate_bias_predictions.py --start 2025-11-01 --end 2025-11-30
```

**What it does**:
- Finds predictions where earnings have passed but not yet validated
- Fetches actual price moves using yfinance
- Determines if prediction was correct
- Updates database with validation results

**Validation Logic**:
- **Bullish bias** → Correct if actual direction = UP
- **Bearish bias** → Correct if actual direction = DOWN
- **Neutral bias** → Correct if actual direction = FLAT (|move| < 0.5%)

**Example Output**:
```
CRM: ✓ Validated - predicted strong_bearish, actual DOWN (-3.2%)
AAPL: ✗ Validated - predicted weak_bullish, actual DOWN (-1.1%)
```

### 3. Generate Accuracy Reports

**Script**: `scripts/bias_accuracy_report.py`

```bash
# Full report
python scripts/bias_accuracy_report.py

# Date range report
python scripts/bias_accuracy_report.py --start 2025-11-01 --end 2025-11-30

# Update statistics table
python scripts/bias_accuracy_report.py --update-stats
```

**What it shows**:
- Overall accuracy across all predictions
- Accuracy by bias strength (STRONG, MODERATE, WEAK, NEUTRAL)
- Accuracy by confidence level (HIGH >0.7, MED 0.3-0.7, LOW <0.3)
- Accuracy by direction (BULLISH, BEARISH, NEUTRAL)
- Recent predictions (last 20)

**Example Output**:
```
📊 OVERALL PERFORMANCE
   Total Predictions: 127
   Correct: 89
   Accuracy: 70.1%

📈 BY BIAS STRENGTH
   Level           Name            Total    Correct  Accuracy
   ------------------------------------------------------------
   3               STRONG          23       19       82.6%
   2               MODERATE        45       32       71.1%
   1               WEAK            38       24       63.2%
   0               NEUTRAL         21       14       66.7%

🎯 BY CONFIDENCE LEVEL
   Bucket          Range           Total    Correct  Accuracy
   ------------------------------------------------------------
   HIGH            >0.7            15       13       86.7%
   MEDIUM          0.3-0.7         78       56       71.8%
   LOW             <0.3            34       20       58.8%
```

## Directional Bias Levels

Our system uses a 7-level directional bias scale:

| Level | Bias Strength | Threshold | Interpretation |
|-------|--------------|-----------|----------------|
| STRONG_BEARISH | 3 | \|slope\| > 1.5, slope > 0 | Puts very expensive, strong down bias |
| BEARISH | 2 | 0.8 < \|slope\| ≤ 1.5, slope > 0 | Moderate put skew |
| WEAK_BEARISH | 1 | 0.3 < \|slope\| ≤ 0.8, slope > 0 | Slight bearish tilt |
| NEUTRAL | 0 | \|slope\| ≤ 0.3 | Balanced IV |
| WEAK_BULLISH | 1 | 0.3 < \|slope\| ≤ 0.8, slope < 0 | Slight bullish tilt |
| BULLISH | 2 | 0.8 < \|slope\| ≤ 1.5, slope < 0 | Moderate call skew |
| STRONG_BULLISH | 3 | \|slope\| > 1.5, slope < 0 | Calls very expensive, strong up bias |

**Bias Confidence**:
- Calculated as: `R² × slope_strength`
- Where `slope_strength = min(1.0, |slope| / 2.0)`
- Higher confidence = better polynomial fit + stronger slope

## Key Metrics to Track

### 1. Strength-Stratified Accuracy
**Hypothesis**: STRONG bias should have higher accuracy than WEAK bias

Track:
- STRONG accuracy (expect >75%)
- MODERATE accuracy (expect ~70%)
- WEAK accuracy (expect ~60%)
- NEUTRAL accuracy (baseline)

### 2. Confidence-Stratified Accuracy
**Hypothesis**: High confidence predictions should be more accurate

Track:
- HIGH confidence (>0.7) accuracy
- MEDIUM confidence (0.3-0.7) accuracy
- LOW confidence (<0.3) accuracy

### 3. Directional Balance
**Check for bias**: Are we better at predicting one direction?

Track:
- Bullish accuracy
- Bearish accuracy
- Neutral accuracy

### 4. VRP Context
**Question**: Does high VRP improve bias prediction accuracy?

Track accuracy segmented by:
- VRP > 3.0 (excellent edge)
- VRP 2.0-3.0 (good edge)
- VRP 1.5-2.0 (moderate edge)
- VRP < 1.5 (marginal edge)

## Best Practices

### When to Store Predictions
- **Timing**: 1-3 days before earnings
- **Requirements**: Option chain must have liquid strikes
- **Frequency**: Daily scan of upcoming earnings

### When to Validate
- **Timing**: 1-2 days after earnings announcement
- **Why wait**: Allow time for price to settle
- **Automation**: Run daily to catch all recent earnings

### Interpreting Results

**Good Performance Indicators**:
- Overall accuracy >65%
- STRONG bias accuracy >75%
- High confidence accuracy >80%
- Balanced directional accuracy (no systematic bias)

**Warning Signs**:
- Overall accuracy <60% (model may need tuning)
- STRONG bias accuracy <70% (threshold calibration issue)
- Significant directional imbalance (>15% difference)
- Low confidence predictions outperforming high confidence

## Continuous Improvement

Use validation results to:

1. **Calibrate Thresholds**
   - If STRONG bias accuracy is low, increase threshold (e.g., 1.5 → 1.8)
   - If WEAK bias has good accuracy, decrease threshold (e.g., 0.3 → 0.25)

2. **Adjust Confidence Formula**
   - Weight R² vs slope_strength differently
   - Add volume-weighted data quality metric
   - Consider time-to-expiration adjustment

3. **Refine Strategy Selection**
   - Use validated accuracy to tune strategy mix
   - Adjust position sizing based on confidence
   - Skip trades when bias confidence is very low

4. **Feature Engineering**
   - Test alternative skew metrics (put/call ratio, risk reversal)
   - Incorporate term structure (skew across expirations)
   - Add market regime filters (VIX, breadth)

## Example Workflow

```bash
# Monday: Store predictions for upcoming week
python scripts/store_bias_prediction.py --all

# Daily: Validate past predictions
python scripts/store_bias_prediction.py

# Weekly: Generate accuracy report
python scripts/bias_accuracy_report.py --update-stats

# Monthly: Deep dive analysis
python scripts/bias_accuracy_report.py --start 2025-11-01 --end 2025-11-30
```

## Database Queries

### Find high-confidence predictions that were wrong
```sql
SELECT ticker, earnings_date, directional_bias, bias_confidence,
       actual_direction, actual_move_pct
FROM bias_predictions
WHERE validated_at IS NOT NULL
  AND bias_confidence > 0.7
  AND prediction_correct = 0
ORDER BY bias_confidence DESC;
```

### Track accuracy over time
```sql
SELECT
    DATE(validated_at, 'start of month') as month,
    COUNT(*) as total,
    SUM(prediction_correct) as correct,
    ROUND(AVG(prediction_correct) * 100, 1) as accuracy
FROM bias_predictions
WHERE validated_at IS NOT NULL
GROUP BY month
ORDER BY month;
```

### Best/worst performing bias levels
```sql
SELECT
    directional_bias,
    COUNT(*) as total,
    SUM(prediction_correct) as correct,
    ROUND(AVG(prediction_correct) * 100, 1) as accuracy
FROM bias_predictions
WHERE validated_at IS NOT NULL
GROUP BY directional_bias
HAVING total >= 5
ORDER BY accuracy DESC;
```

## Future Enhancements

1. **Option Chain Snapshots**: Store full option chain before earnings for deeper analysis
2. **Intraday Tracking**: Track how bias changes as earnings approach
3. **Post-Earnings Skew**: Compare pre vs post-earnings skew
4. **Machine Learning**: Train model on validated predictions
5. **Alerts**: Notify when high-confidence predictions are available
6. **Backtesting**: Run historical validation on past earnings

## References

- Main implementation: `src/application/metrics/skew_enhanced.py`
- Strategy integration: `src/application/services/strategy_generator.py`
- Enum definitions: `src/domain/enums.py`
- Code review: `CODE_REVIEW.md`
