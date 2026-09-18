"""ab_evaluate_live.py's sample selection and multiple-comparison handling.

A 2026-09-13 study of the live evaluator found four defects, each tested here:

1. It graded each event's LATEST analysis. For 63 of 227 reactions that log was
   taken after the reaction-day open, when the implied move had already collapsed
   (median 4.22% vs 8.43%, crush 55.6% vs 68.9%), contaminating both A/Bs. It now
   grades the FIRST analysis taken before the open.
2. Its 30-day nearest-date join matched a stale calendar date to the same
   `historical_moves` reaction a second time: 15 of 242 rows repeated a reaction
   already counted, including GOOG beside GOOGL. It now grades one row per issuer
   reaction.
3. Two verdicts shared one alpha with no correction. The VRP test is now named
   primary, the term test secondary, and both are Holm-adjusted as one family.
4. `--full` printed the literal text "11s" where each date belonged.

Pure functions only; the SQL and main() are covered against a real Postgres in
test_ab_evaluate_live_pg.py.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from scripts import ab_evaluate_live as ev  # noqa: E402

ET = ZoneInfo("America/New_York")


def et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def analysis(id_, ticker, logged, ts, impl=8.0, vi=2.0, vc=1.0, slope=None, timing=None):
    return {"id": id_, "ticker": ticker, "timestamp": ts, "earnings_date": logged, "impl": impl,
            "vi": vi, "vc": vc, "ts": slope, "analysis_timing": timing}


def reaction(day, cm=5.0, im=2.0, timing="BMO"):
    return {"earnings_date": day, "cm": cm, "im": im, "timing": timing}


# --- pre-registration pin -----------------------------------------------------

def test_pre_registered_thresholds_are_unchanged():
    """The hygiene fixes change the sample and add a correction, never the bar."""
    assert ev.MIN_EVENTS == 300
    assert ev.MIN_EFFECT_PTS == 3.0
    assert ev.TOP_K_PCT == 20
    assert ev.TERM_MIN_PER_BUCKET == 8
    assert ev.TERM_MIN_GAP_PTS == 10.0
    assert ev.TERM_SPLIT_QUANTILE == pytest.approx(1 / 3)
    assert ev.BOOTSTRAP_N == 20000 and ev.SEED == 7
    assert ev.JOIN_DAYS == 30
    # The alpha the 95%-CI-lower-bound criterion always implied, one-sided.
    assert ev.ALPHA_ONE_SIDED == 0.025


def test_intraday_ladder_is_frozen_at_the_pre_migration_values():
    """D8: the evaluator's diagnostics (term_bands, term_stats' legacy split) grade
    every event on the intraday ladder that was live when this A/B was designed,
    never the live grading ladder -- otherwise a live threshold change would
    silently move a diagnostic that is supposed to describe the *old* population."""
    assert ev.INTRADAY_LADDER == {"excellent": 1.8, "good": 1.4, "marginal": 1.2}


def test_term_bands_grades_vi_on_the_intraday_ladder_not_the_close_one():
    """An intraday vi of 1.3 falls in MARGINAL on INTRADAY_LADDER (1.2 <= 1.3 < 1.4).
    The live close-to-close ladder (VRP_EXCELLENT_FROM 0.99) would call 1.3 EXCELLENT --
    term_bands must not have drifted onto it."""
    known = [{"vi": 1.3, "ts": 1.5}]
    counts = {name: n for name, n, _flat in ev.term_bands(known)}
    assert counts["MARGINAL"] == 1
    assert counts["EXCELLENT"] == 0


# --- 1. pre-open eligibility ----------------------------------------------------

class TestReactionOpenCutoff:
    def test_bmo_cuts_off_at_the_report_dates_open(self):
        assert ev.reaction_open_cutoff(date(2026, 7, 28), "BMO") == et(2026, 7, 28, 9, 30)

    def test_amc_cuts_off_at_the_next_sessions_open(self):
        # Tuesday report, Wednesday reaction.
        assert ev.reaction_open_cutoff(date(2026, 7, 28), "AMC") == et(2026, 7, 29, 9, 30)

    def test_amc_friday_skips_the_weekend(self):
        assert ev.reaction_open_cutoff(date(2026, 7, 31), "AMC") == et(2026, 8, 3, 9, 30)

    @pytest.mark.parametrize("report, reaction", [
        (date(2026, 9, 4), date(2026, 9, 8)),      # Friday before Labor Day -> Tuesday
        (date(2026, 11, 25), date(2026, 11, 27)),  # Wednesday before Thanksgiving -> Friday
        (date(2025, 1, 8), date(2025, 1, 10)),     # the Carter day of mourning closed 01-09
    ])
    def test_amc_before_a_market_holiday_reacts_at_the_next_session(self, report, reaction):
        """The cutoff stepped over weekends only (next_weekday, until 2026-09-14), so
        an AMC report before a holiday was cut off at the closed day's 09:30."""
        assert ev.reaction_open_cutoff(report, "AMC") == et(reaction.year, reaction.month, reaction.day, 9, 30)

    def test_a_log_taken_on_the_holiday_is_still_a_forecast(self):
        """Labor Day 11:00, before the Tuesday reaction: graded, not dropped as post-open."""
        assert ev.is_pre_open(et(2026, 9, 7, 11, 0), date(2026, 9, 4), "AMC")

    @pytest.mark.parametrize("timing", ["DMH", "UNKNOWN", None])
    def test_unknown_timing_takes_the_earliest_possible_open(self, timing):
        """Conservative: a DMH/UNKNOWN/untagged report is treated as BMO, the
        earliest open that could be its reaction, so no post-open log is admitted."""
        assert ev.reaction_open_cutoff(date(2026, 7, 28), timing) == et(2026, 7, 28, 9, 30)

    def test_winter_open_is_1430_utc(self):
        cutoff = ev.reaction_open_cutoff(date(2026, 12, 8), "BMO")
        assert cutoff.astimezone(timezone.utc) == datetime(2026, 12, 8, 14, 30, tzinfo=timezone.utc)


class TestIsPreOpen:
    def test_one_minute_before_the_open_is_eligible(self):
        assert ev.is_pre_open(et(2026, 7, 28, 9, 29), date(2026, 7, 28), "BMO")

    def test_at_the_open_is_not(self):
        assert not ev.is_pre_open(et(2026, 7, 28, 9, 30), date(2026, 7, 28), "BMO")

    def test_amc_evening_of_the_report_is_still_pre_open(self):
        assert ev.is_pre_open(et(2026, 7, 28, 16, 30), date(2026, 7, 28), "AMC")

    def test_amc_after_the_next_open_is_not(self):
        assert not ev.is_pre_open(et(2026, 7, 29, 11, 0), date(2026, 7, 28), "AMC")

    def test_utc_timestamps_compare_as_instants(self):
        # 13:29 UTC is 09:29 EDT.
        ts = datetime(2026, 7, 28, 13, 29, tzinfo=timezone.utc)
        assert ev.is_pre_open(ts, date(2026, 7, 28), "BMO")
        assert not ev.is_pre_open(ts.replace(minute=31), date(2026, 7, 28), "BMO")


# --- nearest reaction -----------------------------------------------------------

class TestNearestReaction:
    DATES = [date(2026, 4, 28), date(2026, 7, 28)]

    def test_exact_match(self):
        assert ev.nearest_reaction(date(2026, 7, 28), self.DATES) == (date(2026, 7, 28), False)

    def test_stale_date_within_the_window(self):
        assert ev.nearest_reaction(date(2026, 8, 5), self.DATES) == (date(2026, 7, 28), False)

    def test_outside_the_window_is_pending(self):
        assert ev.nearest_reaction(date(2026, 9, 1), self.DATES) == (None, False)

    def test_equidistant_tie_breaks_to_the_earlier_reaction(self):
        dates = [date(2026, 7, 20), date(2026, 7, 30)]
        assert ev.nearest_reaction(date(2026, 7, 25), dates) == (date(2026, 7, 20), True)


# --- 1 + 2. event selection -------------------------------------------------------

class TestSelectEvents:
    def test_grades_the_first_pre_open_analysis_not_the_latest(self):
        analyses = [
            analysis(1, "AAA", date(2026, 7, 28), et(2026, 7, 20, 11, 0), impl=9.0),
            analysis(2, "AAA", date(2026, 7, 28), et(2026, 7, 27, 11, 0), impl=8.0),
            # After the BMO open: the implied move has already collapsed.
            analysis(3, "AAA", date(2026, 7, 28), et(2026, 7, 28, 11, 0), impl=3.0),
        ]
        sel = ev.select_events(analyses, {"AAA": [reaction(date(2026, 7, 28), cm=5.0)]})
        assert [e["analysis_id"] for e in sel.graded] == [1]
        assert sel.graded[0]["impl"] == 9.0
        assert sel.graded[0]["crush"] == 1
        assert sel.stats["post_open_rows"] == 1
        assert sel.stats["matched_rows"] == 3
        assert sel.stats["latest_post_open"] == 1

    def test_reaction_with_no_pre_open_analysis_is_not_graded(self):
        analyses = [analysis(1, "AAA", date(2026, 7, 28), et(2026, 7, 29, 12, 0))]
        sel = ev.select_events(analyses, {"AAA": [reaction(date(2026, 7, 28), timing="AMC")]})
        assert sel.graded == []
        assert sel.stats["no_pre_open"] == 1
        assert [e["ticker"] for e in sel.no_pre_open] == ["AAA"]

    def test_cutoff_uses_the_reactions_timing_not_the_logged_dates(self):
        """Logged 07-27 AMC, stored reaction 07-28 BMO: the same 07-28 open, and the
        reaction row is the one being graded."""
        analyses = [analysis(1, "AAA", date(2026, 7, 27), et(2026, 7, 28, 10, 0), timing="AMC")]
        sel = ev.select_events(analyses, {"AAA": [reaction(date(2026, 7, 28), timing="BMO")]})
        assert sel.graded == []
        assert sel.stats["no_pre_open"] == 1

    def test_stale_calendar_date_does_not_grade_a_reaction_twice(self):
        analyses = [
            analysis(1, "AAA", date(2026, 7, 28), et(2026, 7, 21, 11, 0)),
            analysis(2, "AAA", date(2026, 8, 5), et(2026, 8, 3, 11, 0)),
        ]
        sel = ev.select_events(analyses, {"AAA": [reaction(date(2026, 8, 5), timing="AMC")]})
        assert len(sel.graded) == 1
        assert sel.graded[0]["analysis_id"] == 1          # first pre-open across both
        assert sel.graded[0]["earnings_date"] == date(2026, 8, 5)
        assert sel.graded[0]["logged_date"] == date(2026, 7, 28)
        assert sel.stats["logged_events"] == 2
        assert sel.stats["matched_events"] == 2
        assert sel.stats["stale_date_duplicates"] == 1
        assert sel.stats["reactions"] == 1

    def test_share_classes_of_one_issuer_count_once(self):
        analyses = [
            analysis(1, "GOOGL", date(2026, 7, 23), et(2026, 7, 22, 11, 0)),
            analysis(2, "GOOG", date(2026, 7, 23), et(2026, 7, 21, 11, 0)),
        ]
        reactions = {"GOOGL": [reaction(date(2026, 7, 23), timing="AMC")],
                     "GOOG": [reaction(date(2026, 7, 23), timing="AMC")]}
        sel = ev.select_events(analyses, reactions)
        assert len(sel.graded) == 1
        assert sel.graded[0]["ticker"] == "GOOG"           # earlier of the two
        assert sel.stats["share_class_duplicates"] == 1
        assert sel.stats["share_class_pairs"] == [("GOOG+GOOGL", date(2026, 7, 23))]

    def test_no_reaction_within_the_window_is_pending(self):
        analyses = [analysis(1, "AAA", date(2026, 9, 20), et(2026, 9, 10, 11, 0))]
        sel = ev.select_events(analyses, {"AAA": [reaction(date(2026, 6, 20))]})
        assert sel.graded == []
        assert len(sel.pending) == 1 and sel.pending[0]["cm"] is None
        assert sel.stats["pending"] == 1

    def test_pending_dates_within_the_window_are_one_event(self):
        """No reaction to anchor on yet, but two logged dates a day apart are one
        report (quarters are ~90 days apart): LEN sat in the pending count twice."""
        analyses = [analysis(1, "AAA", date(2026, 9, 16), et(2026, 9, 11, 11, 0)),
                    analysis(2, "AAA", date(2026, 9, 17), et(2026, 9, 8, 11, 0)),
                    analysis(3, "AAA", date(2026, 12, 16), et(2026, 9, 12, 11, 0))]
        sel = ev.select_events(analyses, {})
        assert [e["analysis_id"] for e in sel.pending] == [2, 3]
        assert sel.stats["pending"] == 2
        assert sel.stats["pending_duplicates"] == 1

    def test_ties_are_counted(self):
        analyses = [analysis(1, "AAA", date(2026, 7, 25), et(2026, 7, 10, 11, 0))]
        reactions = {"AAA": [reaction(date(2026, 7, 20)), reaction(date(2026, 7, 30))]}
        sel = ev.select_events(analyses, reactions)
        assert sel.stats["ties"] == 1
        assert sel.graded[0]["earnings_date"] == date(2026, 7, 20)

    def test_term_sample_takes_the_first_pre_open_log_that_measured_the_slope(self):
        """Scan rows logged no term_slope_ratio until Sep 4 2026, so the first pre-open
        log is often slope-less while an /analyze run an hour later has it. Each test grades
        the first pre-open analysis that logged its own variable: the VRP sample was
        always restricted to rows with close VRP, and the term sample now is to rows
        with a slope. Neither choice looks at the outcome."""
        analyses = [
            analysis(1, "AAA", date(2026, 7, 28), et(2026, 7, 20, 11, 0), impl=9.0),
            analysis(2, "AAA", date(2026, 7, 28), et(2026, 7, 27, 11, 0), impl=8.0, slope=1.4),
            analysis(3, "AAA", date(2026, 7, 28), et(2026, 7, 28, 11, 0), impl=3.0, slope=1.2),
        ]
        samples = ev.build_samples(analyses, {"AAA": [reaction(date(2026, 7, 28))]})
        assert [e["analysis_id"] for e in samples["vrp"].graded] == [1]
        assert [e["analysis_id"] for e in samples["term"].graded] == [2]
        assert samples["term"].graded[0]["ts"] == 1.4

    def test_graded_timing_counts(self):
        analyses = [analysis(1, "AAA", date(2026, 7, 28), et(2026, 7, 20, 11, 0)),
                    analysis(2, "BBB", date(2026, 7, 28), et(2026, 7, 20, 11, 0))]
        reactions = {"AAA": [reaction(date(2026, 7, 28), timing="AMC")],
                     "BBB": [reaction(date(2026, 7, 28), timing=None)]}
        sel = ev.select_events(analyses, reactions)
        assert sel.stats["graded_timing"] == {"AMC": 1, "untagged": 1}


# --- 3. multiple comparisons --------------------------------------------------------

class TestHolm:
    def test_smaller_p_is_doubled(self):
        assert ev.holm({"vrp": 0.01, "term": 0.04}) == pytest.approx({"vrp": 0.02, "term": 0.04})

    def test_step_down_is_monotone(self):
        assert ev.holm({"vrp": 0.02, "term": 0.015}) == pytest.approx({"vrp": 0.03, "term": 0.03})

    def test_an_unevaluable_test_still_counts_in_the_family(self):
        assert ev.holm({"vrp": 0.01, "term": None}) == pytest.approx({"vrp": 0.02, "term": 1.0})

    def test_clipped_at_one(self):
        assert ev.holm({"vrp": 0.7, "term": 0.9}) == pytest.approx({"vrp": 1.0, "term": 1.0})


def test_p_at_alpha_is_the_same_rule_as_the_ci_lower_bound():
    """Uncorrected, `p <= 0.025` is exactly the pre-registered `CI lower bound > 0`,
    so the Holm gate is the old criterion plus the correction and nothing else."""
    for n_le0 in range(0, 60):
        diffs = [-1.0] * n_le0 + [1.0] * (2000 - n_le0)
        lo, _hi, p = ev.ci_and_p(diffs)
        assert (lo > 0) == (p <= ev.ALPHA_ONE_SIDED), n_le0


class TestVrpDecision:
    D = {"top_n": 60, "intraday": 60.0, "close": 66.0, "delta": 6.0, "lo": 0.5, "hi": 12.0, "p_le0": 0.02}

    def test_migrates_only_when_the_holm_p_clears_alpha(self):
        assert ev.vrp_decision(self.D, n=320, p_adj=0.02) == "MIGRATE"

    def test_raw_significance_lost_to_the_correction_keeps_intraday(self):
        assert ev.vrp_decision(self.D, n=320, p_adj=0.04).startswith(
            "KEEP INTRADAY (effect not separable from zero")

    def test_small_real_effect(self):
        d = dict(self.D, delta=2.0)
        assert ev.vrp_decision(d, n=320, p_adj=0.01).startswith("KEEP INTRADAY (effect real")

    def test_sample_floor(self):
        assert ev.vrp_decision(self.D, n=250, p_adj=0.001) == "INSUFFICIENT DATA (250/300 events)"


class TestTermDecision:
    TD = {"low_n": 20, "high_n": 40, "gap": 12.0, "lo": 1.0, "hi": 20.0, "p_le0": 0.01, "split": 1.48}

    def test_promotes_only_when_the_holm_p_clears_alpha(self):
        assert ev.term_decision(self.TD, p_adj=0.02).startswith("PROMOTE")

    def test_raw_significance_lost_to_the_correction_stays_observational(self):
        assert ev.term_decision(self.TD, p_adj=0.03).startswith("KEEP OBSERVATIONAL")

    def test_bucket_floor(self):
        assert ev.term_decision(dict(self.TD, low_n=5), p_adj=0.001).startswith("CANNOT EVALUATE")


# --- 4. --full formatting --------------------------------------------------------------

def test_event_row_prints_dates_not_a_format_spec():
    e = {"ticker": "FDX", "earnings_date": date(2026, 6, 24), "logged_date": date(2026, 6, 24),
         "analysed_at": et(2026, 6, 23, 11, 5), "timing": "AMC", "impl": 6.8, "vi": 2.68, "vc": 4.42,
         "ts": None, "cm": -3.5, "crush": 1}
    row = ev.format_event_row(e)
    assert "11s" not in row
    assert "2026-06-24" in row
    assert "06-23 11:05" in row
    assert row.rstrip().endswith("CRUSH")


def test_pending_row_formats():
    e = {"ticker": "AAA", "earnings_date": date(2026, 9, 20), "logged_date": date(2026, 9, 20),
         "analysed_at": et(2026, 9, 10, 11, 0), "timing": None, "impl": 5.0, "vi": 1.5, "vc": 0.8,
         "ts": 1.3, "cm": None}
    row = ev.format_event_row(e)
    assert "2026-09-20" in row and row.rstrip().endswith("pending")


# --- diagnostic: timing tags the chain contradicts ----------------------------------------

class TestSuspectTimingTags:
    """A later log that is pre-open by the tag, on the same expiration, whose implied
    move has collapsed is what a post-event chain under a wrong tag looks like: on
    2026-09-13 nine AMC-tagged reactions (GM, PYPL, RCL, HUM, LOW, BBY, CIEN...)
    collapsed during the report date's session. Reported, never used to grade."""

    def _sel(self, later_impl, later_exp=date(2026, 7, 31)):
        analyses = [
            dict(analysis(1, "AAA", date(2026, 7, 28), et(2026, 7, 27, 11, 0), impl=8.0),
                 expiration=date(2026, 7, 31)),
            dict(analysis(2, "AAA", date(2026, 7, 28), et(2026, 7, 28, 14, 0), impl=later_impl),
                 expiration=later_exp),
        ]
        return ev.select_events(analyses, {"AAA": [reaction(date(2026, 7, 28), timing="AMC")]})

    def test_collapse_on_the_same_expiration_is_flagged(self):
        flagged = ev.suspect_timing_tags(self._sel(later_impl=3.0))
        assert [(f["ticker"], f["earnings_date"], f["timing"]) for f in flagged] == [
            ("AAA", date(2026, 7, 28), "AMC")]
        assert flagged[0]["impl"] == 8.0 and flagged[0]["collapsed_impl"] == 3.0

    def test_modest_drop_is_not(self):
        assert ev.suspect_timing_tags(self._sel(later_impl=6.0)) == []

    def test_different_expiration_is_not_comparable(self):
        assert ev.suspect_timing_tags(self._sel(later_impl=3.0, later_exp=date(2026, 8, 7))) == []


# --- diagnostic: point-in-time VRP ----------------------------------------------------------

class TestPointInTimeVrp:
    def test_uses_only_quarters_before_the_reaction(self):
        history = [reaction(date(2025, 4, 1), im=-2.0), reaction(date(2025, 7, 1), im=2.0),
                   reaction(date(2025, 10, 1), im=4.0), reaction(date(2026, 1, 1), im=-4.0),
                   reaction(date(2026, 4, 1), im=50.0)]          # the event itself
        assert ev.pit_intraday_vrp(6.0, history, date(2026, 4, 1)) == pytest.approx(2.0)

    def test_twelve_quarter_lookback(self):
        history = [reaction(date(2020 + i // 4, 1 + 3 * (i % 4), 1), im=100.0 if i < 4 else 2.0)
                   for i in range(16)]
        assert ev.pit_intraday_vrp(4.0, history, date(2025, 1, 1)) == pytest.approx(2.0)

    def test_fewer_than_four_quarters_is_none(self):
        history = [reaction(date(2025, m, 1), im=2.0) for m in (1, 4, 7)]
        assert ev.pit_intraday_vrp(4.0, history, date(2026, 1, 1)) is None
