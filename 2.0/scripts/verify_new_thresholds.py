#!/usr/bin/env python3
"""
Verify new VRP thresholds against grid scan results.
Shows how opportunities are now classified.
"""

# Grid scan results
OPPORTUNITIES = [
    ("AKAM", 15.78, "EXCELLENT"),
    ("ADBE", 11.37, "EXCELLENT"),
    ("DVN", 11.03, "EXCELLENT"),
    ("AIG", 10.33, "EXCELLENT"),
    ("HPE", 7.14, "EXCELLENT"),
    ("HPQ", 6.71, "GOOD"),
    ("CSX", 6.22, "GOOD"),
    ("CRM", 6.11, "GOOD"),
    ("COST", 5.95, "GOOD"),
    ("AVGO", 5.49, "GOOD"),
    ("GS", 4.39, "GOOD"),
    ("AEP", 3.72, "MARGINAL"),
    ("BAC", 3.70, "MARGINAL"),
    ("C", 3.53, "MARGINAL"),
    ("BK", 3.07, "MARGINAL"),
    ("CCL", 2.55, "MARGINAL"),
    ("GIS", 2.34, "MARGINAL"),
    ("DRI", 1.77, "MARGINAL"),
]

# New thresholds
EXCELLENT = 7.0
GOOD = 4.0
MARGINAL = 1.5


def classify_vrp(vrp: float) -> str:
    """Classify VRP with new thresholds."""
    if vrp >= EXCELLENT:
        return "EXCELLENT"
    elif vrp >= GOOD:
        return "GOOD"
    elif vrp >= MARGINAL:
        return "MARGINAL"
    else:
        return "POOR"


def main():
    print("=" * 80)
    print("VRP THRESHOLD VERIFICATION")
    print("=" * 80)

    print(f"\n📊 New Thresholds (Data-Driven):")
    print(f"  EXCELLENT: >= {EXCELLENT:.1f}x")
    print(f"  GOOD:      >= {GOOD:.1f}x")
    print(f"  MARGINAL:  >= {MARGINAL:.1f}x")
    print(f"  POOR:      < {MARGINAL:.1f}x")

    print("\n" + "=" * 80)
    print("RECLASSIFICATION RESULTS")
    print("=" * 80)

    # Reclassify
    results = []
    for ticker, vrp, old_rating in OPPORTUNITIES:
        new_rating = classify_vrp(vrp)
        results.append((ticker, vrp, old_rating, new_rating))

    # Show all results
    print(f"\n{'Ticker':<8} {'VRP':>8} {'Old Rating':<12} {'New Rating':<12} {'Change'}")
    print("-" * 60)
    for ticker, vrp, old, new in results:
        change = "" if old == new else "↓" if new < old else "↑"
        print(f"{ticker:<8} {vrp:>7.2f}x {old:<12} {new:<12} {change}")

    # Summary stats
    excellent = sum(1 for _, _, _, new in results if new == "EXCELLENT")
    good = sum(1 for _, _, _, new in results if new == "GOOD")
    marginal = sum(1 for _, _, _, new in results if new == "MARGINAL")
    poor = sum(1 for _, _, _, new in results if new == "POOR")
    total = len(results)

    print("\n" + "=" * 80)
    print("DISTRIBUTION COMPARISON")
    print("=" * 80)

    print(f"\n📊 OLD Thresholds (2.0x / 1.5x / 1.2x):")
    print(f"  EXCELLENT: 17 / 18 (94.4%)")
    print(f"  GOOD:       1 / 18 (5.6%)")
    print(f"  MARGINAL:   0 / 18 (0.0%)")
    print(f"  POOR:       0 / 18 (0.0%)")

    print(f"\n📊 NEW Thresholds ({EXCELLENT:.1f}x / {GOOD:.1f}x / {MARGINAL:.1f}x):")
    print(f"  EXCELLENT: {excellent:2d} / {total} ({excellent/total*100:.1f}%)")
    print(f"  GOOD:      {good:2d} / {total} ({good/total*100:.1f}%)")
    print(f"  MARGINAL:  {marginal:2d} / {total} ({marginal/total*100:.1f}%)")
    print(f"  POOR:      {poor:2d} / {total} ({poor/total*100:.1f}%)")

    print("\n" + "=" * 80)
    print("IMPACT ANALYSIS")
    print("=" * 80)

    print("\n✅ IMPROVEMENTS:")
    print(f"  1. Better Differentiation: {excellent} excellent, {good} good, {marginal} marginal")
    print(f"  2. Balanced Distribution: ~{excellent/total*100:.0f}% / {good/total*100:.0f}% / {marginal/total*100:.0f}%")
    print(f"  3. Focus on Quality: Top {excellent} tickers have VRP >= 7.0x")

    print("\n📈 TOP TIER (EXCELLENT - VRP >= 7.0x):")
    for ticker, vrp, _, new in results:
        if new == "EXCELLENT":
            print(f"  {ticker:6s}: {vrp:6.2f}x")

    print("\n📊 STRONG (GOOD - VRP >= 4.0x):")
    for ticker, vrp, _, new in results:
        if new == "GOOD":
            print(f"  {ticker:6s}: {vrp:6.2f}x")

    print("\n📉 BASELINE (MARGINAL - VRP >= 1.5x):")
    for ticker, vrp, _, new in results:
        if new == "MARGINAL":
            print(f"  {ticker:6s}: {vrp:6.2f}x")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
