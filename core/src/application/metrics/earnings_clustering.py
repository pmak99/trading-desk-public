"""
Earnings Move Clustering - Regime Detection for Options Pricing.

Uses Gaussian Mixture Models (GMM) to identify distinct "regimes"
in a stock's historical earnings move patterns.

Key insight: Many stocks exhibit bi-modal or multi-modal earnings behavior:
- "Normal" quarters: 3-5% moves
- "Event" quarters: 10-15% moves (product launches, guidance changes)

Identifying which regime the market is pricing helps assess:
- Is implied move pricing "normal" or "event" regime?
- What's the probability of each regime?
- Is there edge in disagreeing with market's regime pricing?

Usage:
    clusterer = EarningsClusterer(db_path)

    # Analyze move regimes
    regimes = clusterer.analyze_regimes("AAPL")
    # regimes.n_regimes = 2 (bi-modal)
    # regimes.regime_means = [4.2, 12.5]
    # regimes.regime_probs = [0.75, 0.25]

    # Check if implied move matches expected regime
    assessment = clusterer.assess_implied_move("AAPL", implied_move=8.0)
    # assessment.likely_regime = "event" (8% closer to 12.5% than 4.2%)
    # assessment.market_pricing = "between_regimes" (unclear which)
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, List, Tuple
import numpy as np

logger = logging.getLogger(__name__)

# Optional sklearn import - fall back to simpler method if not available
try:
    from sklearn.mixture import GaussianMixture
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    logger.warning("sklearn not installed, using simplified clustering")


@dataclass
class MoveRegime:
    """
    A distinct earnings move regime (cluster).

    Attributes:
        regime_id: Numeric identifier (0, 1, 2, ...)
        label: Descriptive label (small/normal/large)
        mean_move: Average absolute move in this regime
        std_move: Standard deviation within regime
        probability: Historical frequency of this regime
        move_range: (min, max) typical range for this regime
        recent_count: How many of last 8 quarters were in this regime
    """
    regime_id: int
    label: str
    mean_move: float
    std_move: float
    probability: float
    move_range: Tuple[float, float]
    recent_count: int


@dataclass
class RegimeAnalysis:
    """
    Complete regime analysis for a ticker.

    Attributes:
        ticker: Stock symbol
        n_regimes: Number of distinct regimes identified
        regimes: List of MoveRegime objects
        is_multimodal: True if >1 regime with significant probability
        dominant_regime: Most likely regime
        bic_score: Bayesian Information Criterion (lower = better fit)
        sample_size: Number of historical moves analyzed
        recent_regime_trend: Is recent regime different from historical?
        confidence: Confidence in regime classification (0-1)
    """
    ticker: str
    n_regimes: int
    regimes: List[MoveRegime]
    is_multimodal: bool
    dominant_regime: MoveRegime
    bic_score: float
    sample_size: int
    recent_regime_trend: str
    confidence: float


@dataclass
class ImpliedMoveAssessment:
    """
    Assessment of where implied move falls in regime landscape.

    Attributes:
        ticker: Stock symbol
        implied_move: The implied move being assessed
        closest_regime: Regime closest to implied move
        regime_distance: How many std devs from closest regime mean
        between_regimes: True if implied move is between regime centers
        pricing_interpretation: Human-readable interpretation
        regime_probabilities: Probability of each regime given implied move
        mispricing_signal: Positive = cheap, Negative = expensive
    """
    ticker: str
    implied_move: float
    closest_regime: MoveRegime
    regime_distance: float
    between_regimes: bool
    pricing_interpretation: str
    regime_probabilities: List[float]
    mispricing_signal: float


class EarningsClusterer:
    """
    Identifies distinct regimes in earnings move patterns.

    Uses Gaussian Mixture Models to find natural clusters in
    historical earnings moves, then assesses whether current
    implied moves align with historical regimes.

    Approach:
    1. Load historical absolute moves for ticker
    2. Fit GMM with 1, 2, 3 components
    3. Select best model using BIC
    4. Label regimes (small/normal/large based on means)
    5. Compare implied move to regime landscape
    """

    # Minimum moves needed for reliable clustering
    MIN_MOVES = 12

    # BIC improvement needed to add a regime
    BIC_THRESHOLD = 2.0

    # Regime labels based on move size
    REGIME_LABELS = {
        0: "small",
        1: "normal",
        2: "large",
        3: "extreme"
    }

    def __init__(self, db_path: Path | str):
        """
        Initialize earnings clusterer.

        Args:
            db_path: Path to ivcrush.db database
        """
        self.db_path = Path(db_path)

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def analyze_regimes(
        self,
        ticker: str,
        max_regimes: int = 3,
        min_probability: float = 0.1
    ) -> RegimeAnalysis:
        """
        Analyze earnings move regimes for a ticker.

        Args:
            ticker: Stock symbol
            max_regimes: Maximum regimes to consider (default: 3)
            min_probability: Minimum probability to count as distinct regime

        Returns:
            RegimeAnalysis with identified regimes
        """
        logger.info(f"Analyzing earnings move regimes for {ticker}")

        # Load historical moves
        moves = self._load_historical_moves(ticker)

        if len(moves) < self.MIN_MOVES:
            logger.warning(
                f"{ticker}: Only {len(moves)} moves, need {self.MIN_MOVES} for clustering"
            )
            return self._single_regime_fallback(ticker, moves)

        # Use absolute moves for clustering
        abs_moves = np.abs(moves)

        # Fit GMM models
        if HAS_SKLEARN:
            best_model, n_regimes, bic = self._fit_gmm(abs_moves, max_regimes)
            regimes = self._extract_regimes(best_model, abs_moves, min_probability)
        else:
            # Simplified clustering without sklearn
            regimes, bic = self._simple_cluster(abs_moves)
            n_regimes = len(regimes)

        # Sort regimes by mean move (ascending)
        regimes.sort(key=lambda r: r.mean_move)

        # Re-label after sorting
        for i, regime in enumerate(regimes):
            regime.regime_id = i
            if len(regimes) == 1:
                regime.label = "normal"
            elif len(regimes) == 2:
                regime.label = "small" if i == 0 else "large"
            else:
                regime.label = self.REGIME_LABELS.get(i, f"regime_{i}")

        # Determine if multimodal
        significant_regimes = [r for r in regimes if r.probability >= min_probability]
        is_multimodal = len(significant_regimes) > 1

        # Find dominant regime
        dominant = max(regimes, key=lambda r: r.probability)

        # Check recent regime trend
        recent_trend = self._analyze_recent_trend(moves[-8:] if len(moves) >= 8 else moves, regimes)

        # Calculate confidence
        confidence = self._calculate_confidence(len(moves), regimes, bic)

        logger.info(
            f"{ticker}: {n_regimes} regimes identified, "
            f"multimodal={is_multimodal}, dominant={dominant.label}"
        )

        return RegimeAnalysis(
            ticker=ticker,
            n_regimes=n_regimes,
            regimes=regimes,
            is_multimodal=is_multimodal,
            dominant_regime=dominant,
            bic_score=bic,
            sample_size=len(moves),
            recent_regime_trend=recent_trend,
            confidence=confidence,
        )

    def _load_historical_moves(self, ticker: str) -> np.ndarray:
        """Load historical close-to-close moves from database."""
        if not self.db_path.exists():
            return np.array([])

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT close_move_pct
                FROM historical_moves
                WHERE ticker = ?
                  AND close_move_pct IS NOT NULL
                ORDER BY earnings_date DESC
            """, (ticker,))

            rows = cursor.fetchall()
            conn.close()

            return np.array([row['close_move_pct'] for row in rows])

        except Exception as e:
            logger.error(f"Error loading moves for {ticker}: {e}")
            return np.array([])

    def _fit_gmm(
        self,
        moves: np.ndarray,
        max_regimes: int
    ) -> Tuple[any, int, float]:
        """
        Fit GMM models and select best using BIC.

        Returns:
            (best_model, n_components, bic_score)
        """
        X = moves.reshape(-1, 1)

        best_model = None
        best_bic = float('inf')
        best_n = 1

        for n in range(1, min(max_regimes + 1, len(moves) // 4)):
            try:
                gmm = GaussianMixture(
                    n_components=n,
                    covariance_type='full',
                    random_state=42,
                    n_init=3
                )
                gmm.fit(X)
                bic = gmm.bic(X)

                # Only accept more components if BIC improves enough
                if bic < best_bic - self.BIC_THRESHOLD:
                    best_bic = bic
                    best_model = gmm
                    best_n = n

            except Exception as e:
                logger.debug(f"GMM with {n} components failed: {e}")
                continue

        return best_model, best_n, best_bic

    def _extract_regimes(
        self,
        gmm,
        moves: np.ndarray,
        min_probability: float
    ) -> List[MoveRegime]:
        """Extract regime information from fitted GMM."""
        regimes = []

        n_components = gmm.n_components
        means = gmm.means_.flatten()
        variances = gmm.covariances_.flatten() if gmm.covariance_type == 'full' else gmm.covariances_
        weights = gmm.weights_

        # Get regime assignments for moves
        X = moves.reshape(-1, 1)
        assignments = gmm.predict(X)

        # Count recent assignments (last 8 moves)
        recent_assignments = assignments[-8:] if len(assignments) >= 8 else assignments

        for i in range(n_components):
            # Skip very low probability regimes
            if weights[i] < min_probability / 2:
                continue

            mean = float(means[i])

            # Handle variance extraction
            if gmm.covariance_type == 'full':
                std = float(np.sqrt(gmm.covariances_[i][0][0]))
            else:
                std = float(np.sqrt(variances[i]))

            recent_count = int(np.sum(recent_assignments == i))

            regimes.append(MoveRegime(
                regime_id=i,
                label="",  # Will be set after sorting
                mean_move=mean,
                std_move=std,
                probability=float(weights[i]),
                move_range=(max(0, mean - 2*std), mean + 2*std),
                recent_count=recent_count,
            ))

        return regimes

    def _simple_cluster(self, moves: np.ndarray) -> Tuple[List[MoveRegime], float]:
        """
        Simple clustering fallback when sklearn not available.

        Uses quartile-based segmentation.
        """
        q25 = np.percentile(moves, 25)
        q75 = np.percentile(moves, 75)

        small_moves = moves[moves <= q25]
        normal_moves = moves[(moves > q25) & (moves <= q75)]
        large_moves = moves[moves > q75]

        regimes = []

        # Only create regime if has moves
        if len(small_moves) >= 3:
            regimes.append(MoveRegime(
                regime_id=0,
                label="small",
                mean_move=float(np.mean(small_moves)),
                std_move=float(np.std(small_moves)),
                probability=len(small_moves) / len(moves),
                move_range=(0, float(q25)),
                recent_count=0,
            ))

        if len(normal_moves) >= 3:
            regimes.append(MoveRegime(
                regime_id=1,
                label="normal",
                mean_move=float(np.mean(normal_moves)),
                std_move=float(np.std(normal_moves)),
                probability=len(normal_moves) / len(moves),
                move_range=(float(q25), float(q75)),
                recent_count=0,
            ))

        if len(large_moves) >= 3:
            regimes.append(MoveRegime(
                regime_id=2,
                label="large",
                mean_move=float(np.mean(large_moves)),
                std_move=float(np.std(large_moves)),
                probability=len(large_moves) / len(moves),
                move_range=(float(q75), float(np.max(moves))),
                recent_count=0,
            ))

        # BIC approximation (not real, just for consistency)
        bic = len(moves) * np.log(np.var(moves))

        return regimes, float(bic)

    def _single_regime_fallback(
        self,
        ticker: str,
        moves: np.ndarray
    ) -> RegimeAnalysis:
        """Create single-regime analysis when insufficient data."""
        if len(moves) > 0:
            mean = float(np.mean(np.abs(moves)))
            std = float(np.std(np.abs(moves)))
        else:
            mean = 5.0  # Default assumption
            std = 2.5

        regime = MoveRegime(
            regime_id=0,
            label="normal",
            mean_move=mean,
            std_move=std,
            probability=1.0,
            move_range=(max(0, mean - 2*std), mean + 2*std),
            recent_count=len(moves),
        )

        return RegimeAnalysis(
            ticker=ticker,
            n_regimes=1,
            regimes=[regime],
            is_multimodal=False,
            dominant_regime=regime,
            bic_score=0.0,
            sample_size=len(moves),
            recent_regime_trend="unknown",
            confidence=min(0.5, len(moves) / self.MIN_MOVES),
        )

    def _analyze_recent_trend(
        self,
        recent_moves: np.ndarray,
        regimes: List[MoveRegime]
    ) -> str:
        """Analyze if recent moves suggest regime shift."""
        if len(recent_moves) < 4 or len(regimes) < 2:
            return "stable"

        abs_recent = np.abs(recent_moves)
        recent_mean = float(np.mean(abs_recent))

        # Compare to regime means
        closest_regime = min(
            regimes,
            key=lambda r: abs(r.mean_move - recent_mean)
        )

        # If recent moves cluster around non-dominant regime
        dominant = max(regimes, key=lambda r: r.probability)

        if closest_regime.regime_id != dominant.regime_id:
            if closest_regime.mean_move > dominant.mean_move:
                return "shifting_larger"
            else:
                return "shifting_smaller"

        return "stable"

    def _calculate_confidence(
        self,
        sample_size: int,
        regimes: List[MoveRegime],
        bic: float
    ) -> float:
        """Calculate confidence in regime analysis."""
        # Sample size factor
        size_factor = min(1.0, sample_size / 20)

        # Regime separation factor
        if len(regimes) >= 2:
            means = sorted([r.mean_move for r in regimes])
            stds = [r.std_move for r in regimes]
            avg_std = np.mean(stds)

            # Separation = how many std devs between regime means
            if avg_std > 0:
                separations = [(means[i+1] - means[i]) / avg_std
                              for i in range(len(means)-1)]
                separation_factor = min(1.0, np.mean(separations) / 2.0)
            else:
                separation_factor = 0.5
        else:
            separation_factor = 0.7  # Single regime is more certain

        confidence = size_factor * 0.6 + separation_factor * 0.4
        return float(min(1.0, confidence))

    def assess_implied_move(
        self,
        ticker: str,
        implied_move: float,
        regimes: Optional[RegimeAnalysis] = None
    ) -> ImpliedMoveAssessment:
        """
        Assess how implied move relates to historical regimes.

        Args:
            ticker: Stock symbol
            implied_move: Current implied move percentage
            regimes: Pre-computed regime analysis (computed if None)

        Returns:
            ImpliedMoveAssessment with interpretation
        """
        if regimes is None:
            regimes = self.analyze_regimes(ticker)

        abs_implied = abs(implied_move)

        # Find closest regime
        closest = min(
            regimes.regimes,
            key=lambda r: abs(r.mean_move - abs_implied)
        )

        # Calculate distance in std devs
        if closest.std_move > 0:
            distance = (abs_implied - closest.mean_move) / closest.std_move
        else:
            distance = 0.0

        # Check if between regimes
        between = False
        if len(regimes.regimes) >= 2:
            sorted_regimes = sorted(regimes.regimes, key=lambda r: r.mean_move)
            for i in range(len(sorted_regimes) - 1):
                lower = sorted_regimes[i]
                upper = sorted_regimes[i + 1]
                mid_point = (lower.mean_move + upper.mean_move) / 2

                if lower.mean_move < abs_implied < upper.mean_move:
                    # Check if closer to midpoint than to either regime
                    dist_to_lower = abs_implied - lower.mean_move
                    dist_to_upper = upper.mean_move - abs_implied
                    gap = upper.mean_move - lower.mean_move

                    if dist_to_lower > gap * 0.25 and dist_to_upper > gap * 0.25:
                        between = True
                        break

        # Calculate regime probabilities given implied move
        probs = self._calculate_regime_probabilities(abs_implied, regimes.regimes)

        # Generate interpretation
        interpretation = self._generate_interpretation(
            abs_implied, closest, distance, between, regimes
        )

        # Calculate mispricing signal
        # Positive = implied move seems low (potential buy)
        # Negative = implied move seems high (potential sell)
        mispricing = self._calculate_mispricing(
            abs_implied, regimes.dominant_regime, closest, probs
        )

        return ImpliedMoveAssessment(
            ticker=ticker,
            implied_move=implied_move,
            closest_regime=closest,
            regime_distance=distance,
            between_regimes=between,
            pricing_interpretation=interpretation,
            regime_probabilities=probs,
            mispricing_signal=mispricing,
        )

    def _calculate_regime_probabilities(
        self,
        implied_move: float,
        regimes: List[MoveRegime]
    ) -> List[float]:
        """
        Calculate probability of each regime given implied move.

        Uses Bayesian approach: P(regime|move) ∝ P(move|regime) * P(regime)
        """
        if not regimes:
            return []

        # Calculate likelihood * prior for each regime
        scores = []
        for regime in regimes:
            # Gaussian likelihood
            if regime.std_move > 0:
                z = (implied_move - regime.mean_move) / regime.std_move
                likelihood = np.exp(-0.5 * z * z)
            else:
                likelihood = 1.0 if abs(implied_move - regime.mean_move) < 1.0 else 0.1

            score = likelihood * regime.probability
            scores.append(score)

        # Normalize to probabilities
        total = sum(scores)
        if total > 0:
            probs = [s / total for s in scores]
        else:
            probs = [1.0 / len(regimes)] * len(regimes)

        return probs

    def _generate_interpretation(
        self,
        implied_move: float,
        closest: MoveRegime,
        distance: float,
        between: bool,
        regimes: RegimeAnalysis
    ) -> str:
        """Generate human-readable interpretation."""
        parts = []

        if between:
            parts.append(f"Implied move {implied_move:.1f}% is between regime centers")
            parts.append("Market uncertain which regime to price")
        else:
            parts.append(f"Implied move {implied_move:.1f}% aligns with '{closest.label}' regime")
            parts.append(f"(mean: {closest.mean_move:.1f}%, prob: {closest.probability:.0%})")

        if abs(distance) > 1.5:
            if distance > 0:
                parts.append("WARNING: Implied move is unusually high for this regime")
            else:
                parts.append("Note: Implied move is at low end of regime")

        if regimes.is_multimodal:
            parts.append(f"Stock has {regimes.n_regimes} distinct move patterns")

        return "; ".join(parts)

    def _calculate_mispricing(
        self,
        implied_move: float,
        dominant: MoveRegime,
        closest: MoveRegime,
        probs: List[float]
    ) -> float:
        """
        Calculate mispricing signal.

        Positive = options may be cheap (implied < expected)
        Negative = options may be expensive (implied > expected)
        """
        # Expected move based on regime probabilities
        # (weighted average of regime means)
        # For simplicity, compare to dominant regime

        # If implied move is below dominant regime mean, positive signal
        signal = (dominant.mean_move - implied_move) / dominant.mean_move

        # Scale by confidence (probability of dominant regime)
        signal *= dominant.probability

        # Cap signal magnitude
        return float(max(-1.0, min(1.0, signal)))

    def get_regime_summary(self, ticker: str) -> dict:
        """
        Get a simple summary of regime analysis.

        Returns:
            Dict with key regime metrics
        """
        analysis = self.analyze_regimes(ticker)

        return {
            'ticker': ticker,
            'n_regimes': analysis.n_regimes,
            'is_multimodal': analysis.is_multimodal,
            'dominant_regime': analysis.dominant_regime.label,
            'dominant_mean': analysis.dominant_regime.mean_move,
            'regime_means': [r.mean_move for r in analysis.regimes],
            'regime_probs': [r.probability for r in analysis.regimes],
            'sample_size': analysis.sample_size,
            'confidence': analysis.confidence,
            'recent_trend': analysis.recent_regime_trend,
        }
