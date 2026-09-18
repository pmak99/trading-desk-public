# Research Results — September 2026

Two pre-registered studies run 2026-09-12 on the shared research core
(`2.0/scripts/research/events.py`), built in the backtest-integrity work
(`docs/superpowers/plans/2026-09-11-backtest-integrity.md`). A third — small-sample
VRP denominators, audit 4.4 — ran on the same core 2026-09-14 (§3).

**Both studies used the same 5,948-event sample.** The second is therefore
weaker evidence than the first — a second look at the same data. Neither
threshold was adjusted after seeing a result; both scripts were committed
with their criteria fixed *before* being run, and the commit history is the
evidence (`placebo.py` and `percentile_vrp.py` each landed one commit ahead of
their first execution).

§5, VIX-regime threshold multipliers (audit 3.1), ran 2026-09-14 on the same
core (5,946 events after the 2026-09-13 repairs) under the same rule:
`vix_adaptive_thresholds.py` landed in commit `7868455`, one commit ahead of its
first run.

**All figures are a straddle proxy, net of costs.** Outcomes model a short ATM
straddle held to expiry, not the defined-risk next-day-exit spread actually
traded — no historical option chains are stored, only a single ATM straddle
price and OHLC. A straddle's uncapped loss makes the proxy conservative
relative to a capped spread. Never cite these as spread P&L.

---

## 1. Placebo test — **PASS**

**Question.** Does the ranking procedure manufacture an apparent edge? If you
shuffle which realised outcome belongs to which event and re-rank, does top-K
still look good?

**Method.** Reassignment, not resampling: the marginal outcome distribution is
preserved exactly and each event keeps its own VRP, so the only thing destroyed
is the pairing between signal and outcome. 1,000 shuffles, seed 7.

**Pre-registered bar.** Gap ≥ 5.0 pts **and** the real top-20% crush rate above
the shuffled 97.5th percentile. Both required: a large gap against a wide null
is not evidence, and clearing a narrow null by half a point is not material.

| | |
|---|---|
| Events | 5,948 |
| Top-20% size | 1,190 |
| **Real crush** | **87.7%** |
| Placebo mean | 76.0% |
| Placebo 97.5th pct | 78.2% |
| **Gap** | **+11.8 pts** (bar: ≥ 5.0) |
| **Verdict** | **PASS** — both conditions met |

**Internal consistency check worth noting:** the placebo mean landed at exactly
76.0%, which is the overall base crush rate. That is what *should* happen —
destroying the pairing should make top-K indistinguishable from picking at
random — and it confirms the shuffle is doing what it claims.

**Reading.** The VRP selection edge is real, not an artefact of ranking a
high-base-rate population. This does not say the edge is large enough to trade
on after real spread costs; it says the signal is not fictitious.

---

## 2. Percentile VRP vs ratio VRP — **FAIL, decisively and in the opposite direction**

**Question.** Ratio VRP divides by the *mean* of at most 12 prior absolute
moves — the least robust estimator available on a fat-tailed sample. Does
`P(realised < implied)`, the percentile of its own prior distribution the
market is pricing at, order events better?

**Pre-registered bar.** Gap ≥ 3.0 pts **and** bootstrap 95% CI lower bound
above zero (20,000 resamples, seed 7).

| | |
|---|---|
| Ratio-VRP top-20% crush | **87.7%** |
| Percentile-VRP top-20% crush | 77.6% |
| **Gap** | **−10.2 pts** (bar: ≥ +3.0) |
| Bootstrap 95% CI | **[−11.8, −6.6]** |
| P(Δ ≤ 0) | 1.000 |
| **Verdict** | **FAIL** |

The CI sits entirely below zero, so this is not "no effect" — percentile VRP is
**significantly worse** than the ratio it was meant to improve on.

### Why: the metric saturates, and more data cannot fix it

| | Ratio VRP | Percentile VRP |
|---|---|---|
| Distinct values across 5,948 events | **5,943** | **35** |
| Events pinned at the maximum | — | **3,935 (66.2%)** |

The top-20% cut needs 1,190 events. **3,935 are tied at percentile = 1.0** —
the straddle exceeded *every* prior move. Ranking therefore selects an
arbitrary 1,190 of those 3,935 and discards the ordering information the ratio
retains, precisely within the high-VRP group that matters most.

The cause is structural. Percentile resolution is capped at `100/n`, and `n` is
per-ticker history depth, which the data holds between 4 and 12 quarters:

```
prior-history depth: {4: 713, 5: 710, 6: 714, 7: 708, 8: 704,
                      9: 702, 10: 701, 11: 682, 12: 314}
```

Mean resolution is 13.0 points per step. Because straddles usually exceed
historical moves — that *is* the IV premium the whole strategy monetises — most
events land at the ceiling.

**This is not a sample-size problem.** More events add more observations at the
same 35 possible values; they do not add resolution, because resolution is set
by per-ticker history depth. Structurally the same shape as the retired
term-structure threshold finding in CLAUDE.md: an instrument that cannot
discriminate at any sample size, for a reason visible in the distribution
rather than in the p-value.

### Consequences

- **Do not revisit percentile VRP** on the strength of "more data will help."
  It will not. Revisit only with materially deeper per-ticker history (20+
  quarters), which the universe does not have.
- **The median / winsorised-denominator idea is also settled** by this study,
  which superseded it. A percentile is invariant to mean-vs-median by
  construction, so testing the percentile tested that family. Worth recording
  the trap it avoided: median < mean on fat tails, so a median denominator
  would *lower* the VRP denominator and *inflate* every ratio — loosening the
  sizing gate in the same dangerous direction as the duplicate-row bug.
- **Ratio VRP stays.** It survived a direct challenge, which is more than it
  had before.

---

## 3. Small-sample VRP denominators (audit 4.4, 2026-09-14) — **FAIL, both estimators**

**Question.** `MIN_HISTORICAL_QUARTERS` is 4, so a name with 4–7 quarters of
history is tradeable on a mean of 4–7 fat-tailed moves. Does a denominator
built to be robust at that sample size order those events' crush better than
the raw mean, at the same selection rate?

**Pre-registered** in `2.0/scripts/research/small_sample_vrp.py`, committed
(`d94c729`) before its first run on real data:

| | |
|---|---|
| PRIMARY | conservative VRP: implied ÷ one-sided 90% t upper bound of the mean prior move |
| SECONDARY | mean shrunk toward a straddle-scaled universe prior, k = 4 pseudo-quarters |
| Not run | median and winsorised means (lower the denominator — §2's dangerous direction) |
| Sample | `load_events(baseline="intraday")`; SMALL = 4–7 prior quarters, CONTROL = 8+ |
| Metric | top-20% crush, candidate − raw, paired bootstrap 20,000, seed 7 |
| Correction | PRIMARY + SECONDARY one Holm family, one-sided α 0.025 |
| Adopt only if | SMALL Δ ≥ +3.0 pts and Holm p ≤ 0.025; CONTROL CI lower bound > −3.0; CONTROL not passing on its own |

**Result, verbatim:**

```
SMALL-SAMPLE VRP DENOMINATORS (audit 4.4) -- straddle proxy, net of costs
pre-registered: PRIMARY=ucb (t 0.90 one-sided UCB), SECONDARY=shrunk (k=4), top 20%, bootstrap 20000 seed 7
  pass: delta >= +3.0 pts and Holm p <= 0.025 in 4-7 quarters; control CI lower > -3.0 pts; universe-wide if control passes too
events 5946; universe prior U = 0.3920 (mean prior move / straddle)

SMALL (4-7 quarters): 2844 events, 2023-07-27 -> 2026-05-14 (median 2024-10-29), top 20% = 569, base crush 77.4%
  ranking  top-K crush delta pts           95% CI  P(d<=0)    AUC net edge overlap tier flips
  raw vrp        90.9%                                      0.615    +4.31
  ucb            90.3%      -0.5 [  -2.1,   +1.2]    0.751  0.620    +4.33   90.2%      47.1%
  shrunk         90.7%      -0.2 [  -1.6,   +1.2]    0.674  0.614    +4.33   93.5%      16.2%

CONTROL (8+ quarters): 3102 events, 2023-03-06 -> 2026-05-22 (median 2025-10-23), top 20% = 620, base crush 74.6%
  ranking  top-K crush delta pts           95% CI  P(d<=0)    AUC net edge overlap tier flips
  raw vrp        85.6%                                      0.581    +4.10
  ucb            84.2%      -1.5 [  -2.6,   +0.3]    0.947  0.581    +3.87   91.6%      36.0%
  shrunk         85.3%      -0.3 [  -1.0,   +0.8]    0.756  0.581    +4.08   96.8%       9.6%

pooled, descriptive: raw top-20% crush 87.7%; ucb -0.6 pts, shrunk -0.3 pts
net edge = mean net_edge() of the top-K, straddle pts; tier flips = units, not a test

PRIMARY ucb: Holm p = 1.000 -> FAIL
SECONDARY shrunk: Holm p = 1.000 -> FAIL
```

**Reading.**

- **Neither estimator helps where it was meant to.** Both point estimates are
  slightly *negative* in the 4–7 quarter stratum and both CIs straddle zero; the
  +3-point bar is outside both intervals, so this is a null with the useful effect
  size ruled out, not an underpowered maybe.
- **The raw mean is not failing short histories.** Raw VRP's top-20% crushes at
  90.9% in the 4–7 stratum against 85.6% at 8+. Depth and era are confounded
  (medians 2024-10 vs 2025-10), so this is not evidence that short histories are
  *better* — only that nothing here shows them worse.
- **The conservative bound re-sorts by dispersion and depth, and the re-sort adds
  nothing** (AUC 0.620 vs 0.615; 90% of its top-20% is raw VRP's). That matches the
  Jun 2026 A/B (consistency flat-to-anti-predictive) and audit 4.3 (no tail
  measure predicts a move beyond the straddle): history dispersion carries no
  crush information the ratio lacks.
- **Nothing live changes.** `MIN_HISTORICAL_QUARTERS` stays 4 and the VRP
  denominator stays the raw mean.

---

## 4. Compound-risk recalibration (audit 4.5, 2026-09-14) — **INSUFFICIENT, by design**

Not a study on the shared core: it grades live `bias_predictions` rows, and only
rows written on or after 2026-09-14, when the 25-delta skew classifier replaced
the one CLAUDE.md §6's FULL 44% / PARTIAL 67% were measured on. Pre-registered in
`2.0/scripts/research/compound_risk_recalibration.py` (`afb062d`) before any such
row existed.

| | |
|---|---|
| Groups | `SizingContext` itself: NEITHER unless `compound_risk_active`; FULL if `bearish_fused_skew`; else PARTIAL |
| TRR | recomputed as of the reaction (`trr_as_of`) — the stored column was NULL from June to 2026-09-14 |
| Crush | implied move of the pre-open `analysis_log` row nearest the prediction > \|close move\| |
| Floor | `MIN_N_PER_GROUP` = 68: 80% power, 44% vs 70% crush, one-sided α 0.0125 (Holm step 1 of 2) |
| Why 70% | live base crush measured by `ab_evaluate_live.py` on 2026-09-14 (69.8%, n=222); 80% would give 34 |
| Tests | FULL vs NEITHER (PRIMARY), PARTIAL vs NEITHER; Holm, one-sided α 0.025 |
| Below the floor | counts and "insufficient", no rate |

**First run, verbatim:**

```
======================================================================
COMPOUND-RISK RECALIBRATION (audit 4.5) -- new skew classifier rows only
======================================================================
db: trading@db.internal:5432/ivcrush
pre-registered: predicted_at >= 2026-09-14 00:00 EDT; MIN_N_PER_GROUP = 68
  = power 0.80 to detect 44% vs 70% crush, one-sided alpha 0.0125 (Holm step 1 of 2)
  (at the ~80% universe baseline it would be 34)

rows: 0  graded: 0  pending: 0  post-open: 0  no pre-open analysis: 0  duplicate reactions: 0

  group       n  status
  FULL        0  insufficient (need 68)
  PARTIAL     0  insufficient (need 68) -- cannot fill while ORATS_ENABLED=false: the sizing alarm never fires
  NEITHER     0  insufficient (need 68)

PRIMARY FULL vs NEITHER: INSUFFICIENT -- no verdict below 68 per group
SECONDARY PARTIAL vs NEITHER: INSUFFICIENT -- no verdict below 68 per group

--- when this becomes runnable (window: 13 weeks before 2026-09-14) ---
rows written: 49  gradable: 44 (3.38/week)  [skew shares use the OLD classifier: a proxy]
  FULL       0 of 44 (0.0%): never at the observed share; at the 95% upper share 6.8%: 295 weeks -> 2032-05-07
           ceiling, any classifier (TRR HIGH or alarm: 29.5%): 68 weeks -> 2028-01-03
  PARTIAL    0 of 44 (0.0%): never at the observed share; at the 95% upper share 6.8%: 295 weeks -> 2032-05-07
           ceiling, any classifier (TRR HIGH and alarm: 0.0%): never
  NEITHER   44 of 44 (100.0%): 20 weeks -> 2027-02-01

Nothing here changes a live threshold.
```

**Reading.**

- **At today's logging rate the FULL row cannot be recalibrated before January
  2028, even if the new classifier called every TRR-HIGH name bearish.** Only
  `trade.sh TICKER` writes a prediction — scan, whisper and the bot do not — and it
  produced 3.4 gradable rows a week over the last 13 weeks. Of those 44, 13 were
  TRR HIGH and 2 carried a bearish fused skew (old classifier); none had both.
- **PARTIAL cannot fill at all while ORATS is off.** It needs the sizing alarm,
  and the alarm reads ORATS `fcst`/`iee`.
- **Nothing recorded compound risk before this audit.** `store_bias_prediction.py`
  stored `trr_level` NULL on every row from June 2026 (a VRP lookup that could
  never succeed gated it), so `compound_risk_active` was false on all of them.
  Fixed 2026-09-14 (`090d7a1`); the recalibration recomputes TRR either way.
- **The binding constraint is sample volume, not the gate.** The A/B evaluator
  grades 222 reactions from scan, whisper and `/analyze` logs since 2026-06-10,
  about 16 a week against 3.4 here. Writing a prediction for each of those would
  bring the FULL ceiling to roughly 14 weeks, at one skew-chain fetch per name —
  but at the old classifier's observed FULL share (0 of 44) it would still take
  years. A proposal, not done here.
- **Done the same day: every analysis path writes a prediction.** Scan, whisper,
  ticker mode, the bot and the digest reuse the skew reading `analyze_ticker`
  already takes (0 extra Tradier calls, measured), and the writer keeps the first
  pre-open row per calendared event. Re-estimated from `analysis_log` over the same
  13 weeks: 16.8 writable reactions a week (the `/analyze` path alone: 3.6, against
  the 3.4 measured above), 13.9 after the 5 of 29 live chains with no 25Δ reading;
  36.7% TRR HIGH. NEITHER reaches 68 around 2026-10-20 and the FULL ceiling around
  2026-12-17. The new classifier read bearish on 1 of 11 TRR HIGH names in a live
  sample of 24 upcoming events (3 of 24 overall, all banks): FULL at that share
  arrives mid-2029, at its 95% upper bound (41%) late April 2027. The skew share,
  not volume, is now the constraint. PARTIAL still cannot fill.

---

## 5. VIX-regime VRP threshold multipliers — **INSUFFICIENT; removed**

**Question.** `/analyze` (and only `/analyze`) multiplied the VRP tier
thresholds by 1.1 at VIX 20–25, 1.2 at 25–30, 1.3 at 30–35, 1.5 at 35–40, and
refused every trade at VIX 40+. The factors were set by hand; scan, whisper and
the Telegram bot never applied them. Do the events the multiplier stops trading
crush less than the events it lets through?

**Pre-registered bar** (`2.0/scripts/research/vix_adaptive_thresholds.py`,
constants at the top). Among events tradeable at the base thresholds (VRP ≥
1.2, live intraday convention), the events demoted to SKIP must crush less than
**(1)** the events kept **and (2)** events in the same VRP bins at VIX < 20,
where the factor is 1.0 — each with a 95% bootstrap lower bound above zero,
resampling as-of VIX *dates* (5,000 resamples, seed 7), on at least 100 demoted
events and 20 dates. (2) is required because demoted events are low-VRP by
construction, so (1) alone would pass on VRP. As-of VIX: the last Yahoo `^VIX`
close strictly before the earnings date.

| VIX band | Factor | Events | Dates | Crush | → SKIP | → MARGINAL |
|---|:-:|--:|--:|--:|--:|--:|
| 40+ | no trade | 4 | 2 | 100.0% | 4 | 0 |
| 35–40 | 1.5 | 61 | 2 | 85.2% | 8 | 5 |
| 30–35 | 1.3 | 97 | 7 | 85.6% | 2 | 3 |
| 25–30 | 1.2 | 232 | 14 | 80.2% | 6 | 9 |
| 20–25 | 1.1 | 909 | 69 | 76.6% | 9 | 33 |
| < 20 | 1.0 | 4,536 | 407 | 75.4% | 0 | 0 |

| Demoted to SKIP (primary) | |
|---|---|
| Demoted | 29 events on 17 dates, crush 86.2% |
| Kept | 5,810 events, crush 76.0% |
| (1) kept − demoted | **−10.2 pts**, CI [−20.5, +1.9] |
| (2) VRP-matched VIX<20 − demoted | 65.9% vs 86.2%: **−20.3 pts**, CI [−32.2, −8.2] |
| **Verdict** | **INSUFFICIENT** — 29 events on 17 dates, below 100 and 20 |

Secondary (never decisive), full size demoted to MARGINAL: 50 events on 28
dates, crush 80.0% vs kept 76.3%; kept − demoted −3.7 pts [−13.2, +6.1];
matched − demoted −9.8 pts [−20.8, +1.1].

**Reading.** The sample cannot support the multiplier: three years hold 29
demoted events, 12 of them on 2024-08-05/06/07 alone. What there is points the
other way — the demoted events crushed *more* often than the events kept, and
than the same VRP at a calm VIX, and every band above VIX 20 crushed more often
than events at VIX < 20 did.
That is not evidence *against* a VIX term (it fails the same size bar) but it
is no support for raising thresholds, let alone for refusing to trade at 40+.

**Decision.** Removed from the analyzer, so every path applies the CLAUDE.md §1
tiers with no VIX term. VIX 40+ no longer blocks `/analyze`; the scan path never
blocked. The regime is still fetched for display and logged to
`analysis_log.vix_level`.

**Data check.** Yahoo's `^VIX` closes match CBOE's official history on 816 of
817 common days; the exception is 2026-02-06 (Yahoo 20.37, CBOE 17.76), which
moves no demoted event. Re-test only with materially more high-VIX earnings
dates, not by re-cutting these.

## 6. Power of the live VRP-denominator gate (2026-09-16) — **the gate cannot decide**

**Question.** `ab_evaluate_live.py` migrates the VRP denominator to close-to-close
only at ≥300 graded reactions, a top-20% crush delta ≥ +3.0 pts, and a Holm-adjusted
one-sided p ≤ 0.025 across its two-test family. If the universe backtest's effect
is the truth, how often does that gate say MIGRATE? This is a power calculation,
not a test: it has no pass bar, decides nothing, and changes no pre-registered
criterion. `2.0/scripts/research/ab_vrp_power.py`, seed 7.

**Effect assumed true.** The 5,946 matched backtest events (2023-03-06 →
2026-05-22, 12-quarter lookback, raw VRP ranks, re-run 2026-09-16 after the
09-14/15 move corrections): top-20% crush 87.7% → 91.1%, **+3.4 pts**.

**Two noise models.** (1) *Universe*, simulated end to end: 400 draws of n events
from the backtest population, each graded by the live decision with 1,000
bootstrap resamples. (2) *Live*: the delta's SD at n from resampling the 222 graded
live reactions (base crush 69.4%), power by normal approximation, ignoring the +3
floor (so ceilings). Holm over two tests puts VRP's adjusted p between p (term
p ≈ 0) and 2p (term p not smaller, the usual case); both are shown.

| n | Universe: P(MIGRATE), 2p | Universe: p alone | Universe SD | Live SD | Live power, 2p | Live power, p alone |
|--:|--:|--:|--:|--:|--:|--:|
| 222 | 2.8% | 4.5% | 4.72 | 7.82 | 3.5% | 6.3% |
| **300** | **1.8%** | **4.5%** | 3.79 | 6.57 | **4.2%** | **7.4%** |
| 600 | 8.5% | 14.0% | 2.82 | 4.74 | 6.3% | 10.6% |
| 1,200 | 16.5% | 26.0% | 2.03 | 3.45 | 10.3% | 16.2% |
| 2,400 | 45.5% | 54.2% | 1.42 | 2.41 | 19.9% | 28.6% |
| 4,800 | 58.2% | 58.2% | 1.00 | 1.75 | 37.4% | 48.4% |

80% power at +3.4 pts under live noise (2p) needs **~10,890 graded reactions**
(the n = 300 SD scaled as 1/√n): about 11 years at the Jul–Sep 2026 pace of ~19 a
week, which is peak season. Live noise is the larger because graded reactions crush
~69% against ~90% in the backtest's top fifth, and a rate nearer one half varies
more. At 4,800 the universe model's two columns meet at 58%: there the +3.0 floor,
not p, is what fails: +3.4 sits only 0.4 SD above it.

**Reading.** At 300, which the Jul–Sep pace reaches around Oct–Nov 2026, the gate
passes 2–7% of the time even if close is better by exactly what the backtest
measured. Its verdict there will almost surely be KEEP INTRADAY whatever is true,
so it is not evidence against close. The live test cannot settle the question at
any sample the project will accumulate. The universe backtest is the only evidence
with the power to decide: +3.4, CI [+1.4, +5.0] over the whole sample, +2.6,
CI [−0.8, +5.6] from 2025-06-01.

**Population caveat (added 2026-09-16, same day).** The universe is the events with a
stored ORATS `pre_earnings_straddle_pct`: 743 tickers, which include only 6 of the 241
tickers in `analysis_log` and 21 of the 91 traded earnings tickers — AAPL, AMD, AMZN,
MU, NVDA, ORCL, PANW and WDAY each have 19–27 moves and no stored straddle. Both
the +3.4 and this study's universe model therefore describe mostly other names. The
live model above is on the right population; on it the median intraday/close VRP
is 1.85 against the universe's 1.49, so the names traded lean more on the overnight
gap the intraday denominator drops.

---

## 7. VRP close-to-close calibration (2026-09-16) — **PASS**

Pre-registered and committed before its first run: commit `eb7b99ec`
("Pre-register the live VRP close-to-close calibration and gate; scan scorer
takes vrp_target"), 2026-09-16. `2.0/scripts/research/vrp_close_calibration.py`,
read-only, run once. Full output:

```
VRP close-to-close calibration -- live population, matched selectivity (pre-registered 2026-09-16, plan v2)
live: 246 first-log events; universe: 5946 matched backtest events
median intraday/close VRP: live 1.85, universe 1.49

constant               intraday live->close  admits live  universe->close
VRP_MARGINAL_FROM          1.20        0.71  98.8%->99.2%             0.77
VRP_GOOD_FROM              1.40        0.78  96.7%->96.7%             0.89
VRP_EXCELLENT_FROM         1.80        0.99  83.7%->83.7%             1.11
target_vrp                 2.00        1.07  76.8%->76.8%             1.23
SCORE_VRP_TARGET           4.00        2.16  score>=55: 72.0% -> 71.5%

-- decides nothing --
live tier counts, rows intraday tier, columns close tier:
             EXCELLENT      GOOD  MARGINAL      SKIP
  EXCELLENT        180        23         3         0
  GOOD              23         8         1         0
  MARGINAL           2         1         1         1
  SKIP               1         0         1         1
universe >= 2025-06-01 (n=2491) top 20% crush: today 85.7%  proposed 88.2%
live graded reactions (n=222) top 20% crush: today 68.2%  proposed 70.5%
live share with close VRP >= 1.00: 83.3%
universe top 20% mean gross edge: today +4.15  proposed +4.24

G0 live events >= 200: PASS (246)
G1 universe top 20% crush, proposed >= today: PASS (90.8% vs 87.6%)
G2 live score>=55 share within 5 pts: PASS (71.5% vs 72.0%)
CONSTANT VRP_MARGINAL_FROM = 0.71
CONSTANT VRP_GOOD_FROM = 0.78
CONSTANT VRP_EXCELLENT_FROM = 0.99
CONSTANT target_vrp = 1.07
CONSTANT SCORE_VRP_TARGET = 2.16
GATE: PASS
```
Exit code: 0.

**D1 — population.** The mapping ran on the LIVE population, not the universe
backtest: the first `analysis_log` row per (ticker, earnings_date) carrying an
implied move, intraday VRP and close VRP, 246 events, above the `MIN_LIVE_EVENTS`
floor of 200. The universe backtest's 5,946 matched events come from stored ORATS
straddles across 743 tickers, of which only 6 are among the 241 tickers the system
actually scans, so mapping the constants there instead would describe mostly other
names. The known caveat: before 2026-09-04 scans logged only intraday-tradeable
names, so the lowest cutoff (`VRP_MARGINAL_FROM`) is weakly identified on this
population; in this run 98.8% of the 246 live events already clear intraday 1.2,
so few rows are affected by it.

**D2 — ranking changes, selectivity does not.** The live intraday-tier ×
close-tier cross-tab —
```
             EXCELLENT      GOOD  MARGINAL      SKIP
  EXCELLENT        180        23         3         0
  GOOD              23         8         1         0
  MARGINAL           2         1         1         1
  SKIP               1         0         1         1
```
— shows most events keep their tier under the mapped constants, but a material
share move: 23 of 206 intraday-EXCELLENT events read close-GOOD, and 23 of 32
intraday-GOOD events read close-EXCELLENT, so which names clear a tier changes
even though the counts at each cutoff are matched by construction. The printed
1.00-floor line, `live share with close VRP >= 1.00: 83.3%`, is context only —
a close VRP floor was explicitly out of scope for this task and decides nothing
here.

---

## Caveats on the events-core studies

- **Shared sample.** §1, §2, §3 and §5 all ran on the same event sample (5,948 events
  on 2026-09-12, 5,946 on 2026-09-14). Pre-registration limits the damage but does
  not remove it; each later look is weaker evidence than the one before.
- **Straddle proxy.** See the header. Instrument-level P&L is answered by 167
  real Fidelity fills, not by these.
- **Survivorship.** `historical_moves` is built from today's S&P 500 + MidCap
  400 membership, so realised moves are biased low (names that blew up left the
  index), which *inflates* VRP. Fixing it needs point-in-time index
  membership, which the project does not have. This affects every study
  equally and does not change any verdict's direction.

## Reproducing

```bash
cd 2.0
./venv/bin/python scripts/research/placebo.py                  # ~3s
./venv/bin/python scripts/research/percentile_vrp.py           # ~50s
./venv/bin/python scripts/research/small_sample_vrp.py
./venv/bin/python scripts/research/vix_adaptive_thresholds.py  # one Yahoo download
./venv/bin/python scripts/research/ab_vrp_power.py             # ~2 min
./venv/bin/python scripts/research/vrp_close_calibration.py     # ~1 min
```

All are seeded (`SEED = 7`) and deterministic given the same data.
