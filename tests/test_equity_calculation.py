from __future__ import annotations

from datetime import date
import unittest

import api


class EquityCalculationTests(unittest.TestCase):
    def test_mt5_daily_equity_uses_profit_equity_like_official_daily_report(self):
        row = {
            "Login": 1001,
            "Datetime": "2026-06-26T23:59:59Z",
            "Balance": 1000.0,
            "Credit": 100.0,
            "ProfitEquity": -50.0,
        }

        normalized = api.daily_to_dicts([row])[0]

        self.assertEqual(normalized["Equity"], -50.0)
        self.assertEqual(normalized["ProfitEquity"], -50.0)

    def test_explicit_equity_takes_precedence_when_adapter_provides_it(self):
        row = {
            "Balance": 1000.0,
            "Credit": 100.0,
            "ProfitEquity": -50.0,
            "Equity": 1049.5,
        }

        self.assertEqual(api._daily_equity_value(row), 1049.5)

    def test_snapshot_uses_only_the_requested_eod_date(self):
        users = {1001: {"name": "Account One"}}
        rows = api.daily_to_dicts(
            [
                {"Login": 1001, "Datetime": "2026-06-25T23:59:59Z", "Balance": 1000, "Credit": 0, "ProfitEquity": 1025},
                {"Login": 1001, "Datetime": "2026-06-26T23:59:59Z", "Balance": 1000, "Credit": 0, "ProfitEquity": 1030},
                {"Login": 1001, "Datetime": "2026-06-27T23:59:59Z", "Balance": 2000, "Credit": 0, "ProfitEquity": 50},
            ]
        )

        snapshot = api._snapshot_dataframe(rows, users, date(2026, 6, 26))

        self.assertEqual(snapshot.to_dict("records"), [{"Login": 1001, "Name": "Account One", "Equity": 1030.0}])

    def test_daily_summary_sums_official_equity_for_each_report_date(self):
        rows = api.daily_to_dicts(
            [
                {"Login": 1001, "Datetime": "2026-06-26T23:59:59Z", "ProfitEquity": 1000},
                {"Login": 1002, "Datetime": "2026-06-26T23:59:59Z", "ProfitEquity": 500},
                {"Login": 1001, "Datetime": "2026-06-29T23:59:59Z", "ProfitEquity": 1100},
                {"Login": 1002, "Datetime": "2026-06-29T23:59:59Z", "ProfitEquity": 500},
            ]
        )

        summary = api.build_daily_summary_display(rows, date(2026, 6, 27), date(2026, 6, 29))
        records = summary.set_index("Date").to_dict("index")

        self.assertEqual(records["2026-06-27"]["Opening Equity"], 1500.0)
        self.assertEqual(records["2026-06-27"]["Closing Equity"], 0.0)
        self.assertEqual(records["2026-06-28"]["Closing Equity"], 0.0)
        self.assertEqual(records["2026-06-29"]["Opening Equity"], 1500.0)
        self.assertEqual(records["2026-06-29"]["Closing Equity"], 1600.0)


if __name__ == "__main__":
    unittest.main()
