"""quantity_from_legs: a strategy's quantity is per leg, fills summed within it."""

from scripts.strategy_grouper import quantity_from_legs


def test_a_spread_filled_in_parts_counts_contracts_per_leg():
    # 100 a leg, each leg filled as 60 + 40: neither 60 (max over fills) nor 200 (sum).
    assert quantity_from_legs([(100.0, 60), (100.0, 40), (95.0, 60), (95.0, 40)]) == 100


def test_unequal_legs_take_the_larger():
    # A partial exercise or assignment can leave one leg short.
    assert quantity_from_legs([(145.0, 800), (150.0, 300), (150.0, 400)]) == 800


def test_stock_lots_add_up():
    # Every STOCK leg has strike None, so its lots are one group: shares are additive.
    assert quantity_from_legs([(None, 10), (None, 25)]) == 35


def test_missing_quantity_counts_zero_and_no_legs_is_zero():
    assert quantity_from_legs([(100.0, None), (100.0, 5)]) == 5
    assert quantity_from_legs([]) == 0
