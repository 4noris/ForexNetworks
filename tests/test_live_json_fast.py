from datetime import date
import math

import pytest

from services.live_stream import prepare_live_json


def test_plain_live_rows_are_validated_without_copying_or_losing_ticket_precision():
    data = {"positions": [{"ticket": "18446744073709551615", "profit": 1.123456789}], "empty": None}
    assert prepare_live_json(data) is data
    assert data["positions"][0]["profit"] == 1.123456789


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_invalid_money_is_rejected_in_deep_plain_json(value):
    with pytest.raises(ValueError, match="Non-finite"):
        prepare_live_json({"accounts": [{"equity": value}]})


def test_non_plain_reports_keep_existing_conversion_and_invalid_money_checks():
    assert prepare_live_json({"date": date(2026, 10, 28)}) == {"date": "2026-10-28"}
    with pytest.raises(ValueError):
        prepare_live_json({"date": date(2026, 10, 28), "profit": math.inf})
