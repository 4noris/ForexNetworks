from services.symbol_catalog import search_symbols


def test_search_keeps_exact_suffixes_prioritizes_symbols_and_limits_results():
    catalog = {"EURUSD": {"Description": "Gold test"}, "XAUUSD.x": {"Description": "Gold"},
               "XAUUSD": {"Description": "Gold"}, "XAUUSD.i": {"Description": "Gold"}}
    result = search_symbols(catalog, " xauusd ", 2)
    assert result["total"] == 3
    assert [row["symbol"] for row in result["symbols"]] == ["XAUUSD", "XAUUSD.i"]
    assert len(search_symbols(catalog, "gold", 20)["symbols"]) == 4
    assert search_symbols(catalog, "missing", 20)["symbols"] == []
