"""
Dollar-risk warning for oversized positions.

The 100-contract cap (50 in 2.0/.env) is documented as "50 contracts x $500 max
loss = $25K", but that equivalence only holds for a $5-wide spread with no credit.
It silently breaks on expensive underlyings and on singles, where max loss per
contract is the premium paid — a high-priced single was 110 contracts at ~$3,090/contract,
about $340k of risk, while nominally "only" 110 contracts.

This warns on the dollar figure. It never blocks: sizing stays the human's call.
"""


from src.application.services.strategy.sizing import max_loss_warning
from src.config.config import Config, StrategyConfig


class TestThreshold:

    def test_none_when_under_threshold(self):
        assert max_loss_warning(17_500, 50_000, contracts=50) is None

    def test_none_when_exactly_at_threshold(self):
        """Strictly greater-than: a position sized precisely to budget is fine."""
        assert max_loss_warning(50_000, 50_000, contracts=100) is None

    def test_warns_when_over_threshold(self):
        w = max_loss_warning(60_000, 50_000, contracts=100)
        assert w is not None
        assert "60,000" in w
        assert "50,000" in w

    def test_reports_multiple_of_threshold(self):
        w = max_loss_warning(339_900, 50_000, contracts=110)
        assert "6.8x" in w

    def test_reports_per_contract_loss(self):
        """The per-contract figure is the whole point — it shows why the
        contract cap failed to constrain the dollar risk."""
        w = max_loss_warning(339_900, 50_000, contracts=110)
        assert "3,090" in w

    def test_threshold_is_configurable(self):
        assert max_loss_warning(30_000, 25_000, contracts=60) is not None
        assert max_loss_warning(30_000, 100_000, contracts=60) is None


class TestDegenerateInputs:

    def test_zero_max_loss_never_warns(self):
        assert max_loss_warning(0, 50_000, contracts=10) is None

    def test_negative_max_loss_never_warns(self):
        assert max_loss_warning(-100, 50_000, contracts=10) is None

    def test_zero_contracts_does_not_divide_by_zero(self):
        w = max_loss_warning(60_000, 50_000, contracts=0)
        assert w is not None
        assert "per contract" not in w

    def test_zero_threshold_disables_the_warning(self):
        """Threshold <= 0 is the documented off switch."""
        assert max_loss_warning(1_000_000, 0, contracts=100) is None


class TestConfig:

    def test_config_exposes_default_threshold(self):
        cfg = StrategyConfig()
        assert cfg.max_loss_warn_threshold == 50_000.0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("MAX_LOSS_WARN_THRESHOLD", "25000")
        cfg = Config.from_env()
        assert cfg.strategy.max_loss_warn_threshold == 25_000.0

    def test_env_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("MAX_LOSS_WARN_THRESHOLD", raising=False)
        cfg = Config.from_env()
        assert cfg.strategy.max_loss_warn_threshold == 50_000.0


class TestRealPositions:
    """Regression anchors from the actual trade history."""

    def test_expensive_single_warns_loudly(self):
        """110 contracts, ~$3,090/contract premium -> ~$340k risk."""
        assert max_loss_warning(339_900, 50_000, contracts=110) is not None

    def test_ordinary_spread_stays_silent(self):
        """50 contracts x $5-wide less $1.50 credit = $17,500. Must not warn,
        or the signal drowns in noise."""
        assert max_loss_warning(17_500, 50_000, contracts=50) is None

    def test_mu_style_oversized_spread_warns(self):
        """200 contracts x $350/spread = $70,000."""
        assert max_loss_warning(70_000, 50_000, contracts=200) is not None
