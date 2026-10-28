from __future__ import annotations

import unittest

import api


class TradeCountParsingTests(unittest.TestCase):
    def test_summarize_deals_records_counts_type_only_trade_rows(self):
        deals = [
            {
                "Login": 1001,
                "Type": "buy",
                "Volume": 10000,
                "Profit": 2.5,
                "Commission": -0.1,
                "Fee": 0.0,
                "Swap": 0.0,
            },
            {
                "Login": 1001,
                "Type": "sell",
                "Volume": 10000,
                "Profit": -1.0,
                "Commission": -0.2,
                "Fee": 0.0,
                "Swap": 0.0,
            },
            {
                "Login": 1001,
                "Type": "sell",
                "Volume": 10000,
                "Profit": 0.0,
                "Commission": 0.0,
                "Fee": 0.0,
                "Swap": 0.0,
            },
            {
                "Login": 1001,
                "Type": "balance",
                "Volume": 0,
                "Profit": 50.0,
                "Commission": 0.0,
                "Fee": 0.0,
                "Swap": 0.0,
            },
        ]

        report, warnings = api.summarize_deals_records(deals, {1001: "demo"})

        self.assertEqual(warnings, [])
        self.assertEqual(len(report), 1)

        row = report.iloc[0]
        self.assertEqual(int(row["Login"]), 1001)
        self.assertEqual(int(row["Total Trades"]), 3)
        self.assertEqual(int(row["Winning Trades"]), 1)
        self.assertEqual(int(row["Losing Trades"]), 1)
        self.assertEqual(int(row["Breakeven Trades"]), 1)
        self.assertAlmostEqual(float(row["Profit"]), 1.5)


if __name__ == "__main__":
    unittest.main()
