# Analyze Ticker for IV Crush

Perform deep analysis on a specific ticker for IV Crush opportunity.

Arguments: $ARGUMENTS (format: TICKER YYYY-MM-DD)

Example usage:
- /analyze NVDA 2025-12-19
- /analyze TSLA 2025-01-29

Run the analysis:

```bash
cd $PROJECT_ROOT/2.0 && ./trade.sh $ARGUMENTS
```

After running, provide:
1. VRP assessment (ratio and recommendation tier)
2. Implied vs Historical move comparison
3. Liquidity score and any warnings
4. Recommended strategy with strikes and Greeks
5. Risk assessment and position sizing suggestion
