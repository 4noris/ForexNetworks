import math

import api
from mt5_connection import coerce_float


def test_nonfinite_manager_values_cannot_poison_financial_totals():
    for value in ("nan", "inf", "-inf", math.nan, math.inf):
        assert coerce_float(value) == 0.0
        assert api._to_float(value) == 0.0


def test_pick_float_skips_nonfinite_values_and_uses_next_source():
    assert api._pick_float("nan", "inf", "12.5") == 12.5
