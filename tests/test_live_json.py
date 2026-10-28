import math

import orjson
import pytest
from fastapi.encoders import jsonable_encoder

from services.live_stream import finite_float


def test_live_json_preserves_financial_float_and_account_id_precision():
    values={"equity":123456789.1234567,"net_lots":-0.00000001,"login":9007199254740993,"profit":None}
    encoded=jsonable_encoder(values,custom_encoder={float:finite_float})
    assert orjson.loads(orjson.dumps(encoded))==values


@pytest.mark.parametrize("value",[math.nan,math.inf,-math.inf])
def test_live_json_does_not_silently_turn_invalid_financial_numbers_into_null(value):
    with pytest.raises(ValueError,match="Non-finite"):
        jsonable_encoder({"accounts":[{"equity":value}]},custom_encoder={float:finite_float})
