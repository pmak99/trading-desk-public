# ADR-004: Extract Strategy Scoring into Separate Class

## Status
Accepted (November 2024)

## Context
The `StrategyGenerator` class had 180+ lines of complex scoring logic embedded within it (`_score_strategies`, `_generate_strategy_rationale`, `_generate_recommendation_rationale` methods). This created several issues:

### Problems with Embedded Scoring
1. **Testability**: Difficult to test scoring logic independently from strategy generation
2. **Single Responsibility Violation**: StrategyGenerator was responsible for both generation AND scoring
3. **Code Complexity**: 1200+ line class with mixed concerns
4. **Reusability**: Scoring logic couldn't be reused outside StrategyGenerator
5. **Maintainability**: Changes to scoring algorithm required modifying large class

### Scoring Algorithm Complexity
The scoring logic handles:
- 5 weighted factors (POP, R/R, VRP, Greeks, Position Size)
- Two scoring modes (with/without Greeks)
- Weight redistribution when Greeks unavailable
- Three score types (overall, profitability, risk)
- Rationale generation
- Recommendation rationale generation

## Decision
**Extract scoring logic into a separate `StrategyScorer` class in the domain layer.**

### Architecture
```
src/domain/scoring/
├── __init__.py
└── strategy_scorer.py  # New: Isolated scoring logic

src/application/services/
└── strategy_generator.py  # Modified: Uses StrategyScorer
```

### Implementation
```python
class StrategyScorer:
    """Scores and ranks trading strategies."""

    def __init__(self, weights: ScoringWeights | None = None):
        self.weights = weights or ScoringWeights()

    def score_strategy(self, strategy: Strategy, vrp: VRPResult) -> ScoringResult:
        """Score a single strategy."""
        # Scoring logic here

    def score_strategies(self, strategies: List[Strategy], vrp: VRPResult) -> None:
        """Score and rank strategies in-place."""
        # Updates strategy.overall_score, profitability_score, risk_score, rationale
```

### Integration
```python
class StrategyGenerator:
    def __init__(self, config: StrategyConfig):
        self.config = config
        self.scorer = StrategyScorer(config.scoring_weights)  # Inject scorer

    def generate_strategies(...) -> StrategyRecommendation:
        # Generate strategies...
        self.scorer.score_strategies(strategies, vrp)  # Use scorer
        strategies.sort(key=lambda s: s.overall_score, reverse=True)
```

## Consequences

### Positive
✅ **Testability**: Scoring can be tested in isolation with unit tests
✅ **Separation of Concerns**: StrategyGenerator focuses on generation, Scorer focuses on scoring
✅ **Reduced Complexity**: StrategyGenerator reduced from 1273 lines to 1095 lines (14% smaller)
✅ **Reusability**: Scorer can be used independently (e.g., for backtesting analysis)
✅ **Maintainability**: Scoring algorithm changes isolated to one class
✅ **Dependency Injection**: Scoring weights can be injected, enabling custom scoring

### Negative
⚠️ **More Files**: Added 2 files (strategy_scorer.py, test_strategy_scorer.py)
⚠️ **Indirection**: One additional class in the call chain

### Performance Impact
- Negligible: Scorer is instantiated once per StrategyGenerator lifetime
- No additional overhead per score operation

## Testing Benefits

### Before (Embedded Scoring)
To test scoring, you needed to:
1. Create a full StrategyGenerator
2. Create StrategyConfig
3. Create OptionChain
4. Generate full strategies
5. Extract scores

### After (Isolated Scoring)
```python
def test_positive_theta_increases_profitability(scorer, strategy, vrp):
    """Test that positive theta increases profitability score."""
    result1 = scorer.score_strategy(strategy, vrp)

    strategy.position_theta = 50.0
    result2 = scorer.score_strategy(strategy, vrp)

    assert result2.profitability_score > result1.profitability_score
```

Much simpler, faster, and focused.

## Code Metrics

### Lines of Code
- **Removed from StrategyGenerator**: 178 lines
- **Added to StrategyScorer**: 291 lines (includes comprehensive docstrings)
- **Added tests**: 354 lines (comprehensive unit tests)
- **Net**: +467 lines (but with significantly better structure)

### Test Coverage
- **Before**: Scoring tested only through integration tests
- **After**: 15 dedicated unit tests for scoring logic
- **Test Coverage**: ~95% of scoring code paths

### Files Modified
- `src/domain/scoring/__init__.py` (NEW)
- `src/domain/scoring/strategy_scorer.py` (NEW)
- `src/application/services/strategy_generator.py` (MODIFIED)
- `tests/unit/test_strategy_scorer.py` (NEW)

## Migration Path
1. ✅ Create StrategyScorer class with scoring logic
2. ✅ Add StrategyScorer to StrategyGenerator.__init__
3. ✅ Replace _score_strategies with scorer.score_strategies
4. ✅ Replace _generate_recommendation_rationale with scorer.generate_recommendation_rationale
5. ✅ Remove old scoring methods from StrategyGenerator
6. ✅ Add comprehensive unit tests
7. ⏳ Validate with integration tests (ensure no regressions)
8. ⏳ Monitor production for any scoring changes

## Alternatives Considered

### 1. Keep scoring embedded (Status Quo)
**Rejected**: Violates Single Responsibility, difficult to test

### 2. Extract to Strategy method (e.g., strategy.calculate_score())
**Rejected**: Strategies are data classes, shouldn't contain complex business logic

### 3. Make Scorer a static utility class
**Rejected**: Weights would need to be passed to every call, less clean

### 4. Split into multiple scorer classes (POPScorer, RRScorer, etc.)
**Rejected**: Over-engineering for current needs, would complicate weight balancing

## Future Enhancements
1. **Alternative Scoring Algorithms**: Easy to create AlphaScorer, BetaScorer, etc.
2. **A/B Testing**: Deploy multiple scorers, compare results
3. **Machine Learning**: Replace rule-based scoring with ML model
4. **Backtesting**: Analyze historical scoring accuracy independently

## References
- Implementation: `src/domain/scoring/strategy_scorer.py`
- Tests: `tests/unit/test_strategy_scorer.py`
- Integration: `src/application/services/strategy_generator.py:54,101,106`
- Original Issue: Code Review P1.1 (CHANGELOG_REFACTORING.md)

## Deployment Checklist
- [x] Scorer class created and compiles
- [x] StrategyGenerator updated to use scorer
- [x] Old scoring methods removed
- [x] Unit tests created
- [ ] Integration tests pass (run full test suite)
- [ ] Scan.py runs successfully with new scorer
- [ ] Validate scores match previous output (spot check)
- [ ] Monitor production for scoring regressions
