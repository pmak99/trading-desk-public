"""Sentiment is context: it changes no score, cutoff, size or displayed direction.

Until 2026-09-14 an overlay scored it, `4.0 Score = 2.0 Score x (1 + modifier)`,
with +1% for any bullish call and 0% otherwise. Bullish calls were right 35 of
65 (54%), and +1% could move only a ticker whose base score sat in [54.5, 55).
It was dropped (audit step 4.6). Sentiment is still fetched, shown, and
recorded to `sentiment_history`, so it keeps being graded.

The overlay lived as prose in the slash-command files, which nothing executes,
beside constants nothing imported and a contrarian size modifier (0.9x strong
bullish, 1.1x strong bearish) in `common/`. This scans both, so neither can
bring it back quietly.

The same day sentiment stopped adjusting the direction the commands display.
`common/direction.py`'s `adjust_direction` let bullish sentiment break a neutral
skew tie and turn a bearish skew neutral ("hedge both sides"), and /analyze,
/council, /whisper and /postmortem printed the result as the "4.0 Adjusted"
direction. The commands now show the skew reading and the sentiment reading side
by side, neither adjusting the other, and the module is gone.
"""

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMMANDS = REPO / ".claude" / "commands"

SCANNED_DIRS = ["common", "2.0/src", "2.0/scripts", "2.0/tests", "scripts", "tests"]
SKIP_PARTS = {"venv", ".venv", "site-packages", "__pycache__", "node_modules"}

SIZE_MODIFIER = re.compile(r"size[ _]modifier", re.I)   # SIZE_MODIFIER_*, size_modifier

# Each is a shape the overlay took somewhere in the tree.
OVERLAY = [
    re.compile(r"\(\s*1\s*\+\s*modifier\s*\)", re.I),     # the formula
    re.compile(r"\b4\.0 scor", re.I),                      # "4.0 Score", "4.0 SCORING"
    re.compile(r"sentiment[ _]modifier", re.I),            # SENTIMENT_MODIFIER_*, prose
    SIZE_MODIFIER,
    re.compile(r"%\s*modifier", re.I),                     # "+1% modifier"
    re.compile(r'\\?"modifier\\?"'),                       # the key /council cached
    re.compile(r"\b54\.5\b"),                              # the base that +1% lifts to 55
    re.compile(r"\b(pre|post)-filter\b", re.I),            # the two-stage cutoff
]

# Each is a shape the sentiment-adjusted direction took in the commands or common/.
DIRECTION_ADJUSTMENT = [
    re.compile(r"adjust_direction|AdjustedBias|DirectionAdjustment"),
    re.compile(r"common[/.]direction\b"),                                  # the module
    re.compile(r"tiebreak_(bullish|bearish)|conflict_hedge|skew_dominates"),  # its rule names
    re.compile(r"sentiment[- ]adjusted", re.I),
    re.compile(r"\b(4\.0 )?adjusted (direction|bias)\b|\b4\.0 adjusted\b", re.I),
    re.compile(r"\bbreaks? (a|the) neutral (skew )?tie", re.I),
    re.compile(r"hedge both sides|neutral stance", re.I),
    re.compile(r"prefer (bear call|bull put) spreads?", re.I),             # a side chosen by skew
]

# A ranked shortlist cutoff, however it is spelled.
SCORE_CUTOFF = re.compile(r"Score\s*(?:>=|≥|<|below)\s*(\d+(?:\.\d+)?)")
SHORTLIST_COMMANDS = ["whisper.md", "prime.md", "scan.md"]
SCORE_CUTOFF_VALUE = "55"

# Migration 4 dropped sentiment_history.size_modifier, where the contrarian sizing
# stored its multiplier. Its test has to name the column to prove it is gone; a
# line there is excused only when that name is all the scan finds on it.
NAMES_THE_DROPPED_COLUMN = {"2.0/tests/unit/test_migration_004.py"}


def _hits(text: str, patterns) -> list[tuple[int, str]]:
    return [
        (n, line.strip())
        for n, line in enumerate(text.splitlines(), 1)
        if any(p.search(line) for p in patterns)
    ]


def overlay_hits(text: str) -> list[tuple[int, str]]:
    return _hits(text, OVERLAY)


def adjustment_hits(text: str) -> list[tuple[int, str]]:
    return _hits(text, DIRECTION_ADJUSTMENT)


def _python_files():
    this = Path(__file__).resolve()
    for d in SCANNED_DIRS:
        for path in (REPO / d).rglob("*.py"):
            if SKIP_PARTS.isdisjoint(path.parts) and path.resolve() != this:
                yield path


def _excused(name: str, line: str, find) -> bool:
    return name in NAMES_THE_DROPPED_COLUMN and find(SIZE_MODIFIER.sub("", line)) == []


def _tree_hits(find) -> list[str]:
    files = [*sorted(COMMANDS.glob("*.md")), *_python_files()]
    return [
        f"{path.relative_to(REPO)}:{n}: {line}"
        for path in files
        for n, line in find(path.read_text())
        if not _excused(path.relative_to(REPO).as_posix(), line, find)
    ]


def test_only_the_dropped_columns_name_is_excused():
    (name,) = NAMES_THE_DROPPED_COLUMN
    assert (REPO / name).is_file()
    assert _excused(name, 'COLUMN = "size_modifier"', overlay_hits)
    assert not _excused(name, "size = contracts * (1 + modifier)  # size_modifier", overlay_hits)
    assert not _excused("common/constants.py", 'COLUMN = "size_modifier"', overlay_hits)


def test_the_scan_sees_every_shape_the_overlay_shipped_in():
    shipped = [
        "`4.0 Score = 2.0 Score * (1 + modifier)`, modifier per CLAUDE.md §7",
        "4.0 SCORING",
        "SENTIMENT_MODIFIER_BULLISH = 0.01",
        "SIZE_MODIFIER_BEARISH = 1.1",
        "size_modifier=get_size_modifier(sentiment_score),",
        "   Consensus:    +0.42 -> Bullish (+1% modifier)",
        '\'{\\"direction\\": \\"$DIRECTION\\", \\"modifier\\": $MODIFIER}\'',
        "- 2.0 Score ≥54.5 (below that the +1% cannot reach 55)",
        "Filter the whisper output to tickers scoring ≥50 (CLAUDE.md §7 pre-filter).",
    ]
    for line in shipped:
        assert overlay_hits(line), line


def test_the_scan_ignores_ordinary_uses_of_the_words():
    ok = "\n".join([
        "Strip this flag before passing tickers to `trade.sh` — it's a display modifier only.",
        "Direction: [bullish/bearish/neutral]",
        "Score: [number -1 to +1]",
        "   VRP: 4.0x | Implied Move: 5.4%",
    ])
    assert overlay_hits(ok) == []


def test_the_scan_sees_every_shape_the_direction_adjustment_shipped_in():
    shipped = [
        "Apply the directional-bias rules in `common/direction.py` (`adjust_direction`, in order) exactly,",
        "from common.direction import adjust_direction",
        "   Rule: {tiebreak | conflict_hedge | skew_dominates}  (bullish-only tiebreak)",
        "### Step 4: Sentiment-Adjusted Direction (4.0)",
        "DIRECTION (4.0 Adjusted)",
        "   Direction: {2.0 Skew} -> {4.0 Adjusted} ({rule applied})",
        "   {TICKER}: Skew={X} vs Sentiment={Y} -> Neutral stance (hedge both sides)",
        "only bullish sentiment breaks a neutral tie; an actively opposing sentiment goes neutral",
        '     opposed to this signal — prefer bear call spreads or stand aside"]',
        "class AdjustedBias(Enum):",
    ]
    for line in shipped:
        assert adjustment_hits(line), line


def test_the_direction_scan_ignores_ordinary_uses_of_the_words():
    ok = "\n".join([
        "   Skew:      BEARISH — Tradier 25Δ risk reversal",
        "   Sentiment: BULLISH (+0.42)",
        "- **An adjustment (ROLL/REPAIR) links to the position it adjusted by `parent_strategy_id`**",
        "| Separability | **Holm-adjusted one-sided p ≤ 0.025** |",
        "    2. Confirmed-vs-confirmed tiebreak: if two confirmed entries are within",
        "Structure | Bull put or bear call spread. **No iron condors.**",
    ])
    assert adjustment_hits(ok) == []


def test_no_command_file_or_python_module_scores_or_sizes_on_sentiment():
    hits = _tree_hits(overlay_hits)
    assert hits == [], "\n".join(hits)


def test_no_command_file_or_python_module_adjusts_direction_by_sentiment():
    hits = _tree_hits(adjustment_hits)
    assert hits == [], "\n".join(hits)


def test_the_direction_module_and_its_leftovers_are_gone():
    from common import constants
    from src.domain import enums

    assert importlib.util.find_spec("common.direction") is None
    # AdjustedBias was common/enums.py's last member once the domain enums moved
    # to src/domain/enums.py, so the module went with it.
    assert importlib.util.find_spec("common.enums") is None
    assert not hasattr(enums, "AdjustedBias")
    leftovers = [
        n for n in vars(constants)
        if "MODIFIER" in n or n.startswith("HIGH_BULLISH") or n.startswith("ZEROED_SENTIMENT")
    ]
    assert leftovers == []


def test_the_sentiment_label_bands_are_the_ones_the_commands_quote():
    """/collect and /council label a score with these bands; they label, nothing more."""
    from common import constants

    assert (
        constants.SENTIMENT_STRONG_BULLISH_THRESHOLD,
        constants.SENTIMENT_BULLISH_THRESHOLD,
        constants.SENTIMENT_BEARISH_THRESHOLD,
        constants.SENTIMENT_STRONG_BEARISH_THRESHOLD,
    ) == (0.6, 0.2, -0.2, -0.6)


@pytest.mark.skipif(not COMMANDS.is_dir(),
                    reason="the operator's .claude/commands/ definitions are not part of this repository")
def test_every_shortlist_command_states_the_same_single_cutoff():
    """/whisper ranks, /prime pre-caches the same set, /scan picks its top 3: one number, 55."""
    found = {}
    for path in sorted(COMMANDS.glob("*.md")):
        values = SCORE_CUTOFF.findall(path.read_text())
        if values:
            found[path.name] = set(values)
    for name in SHORTLIST_COMMANDS:
        assert name in found, f"{name} states no Score cutoff"
    wrong = {name: v for name, v in found.items() if v != {SCORE_CUTOFF_VALUE}}
    assert wrong == {}, wrong
