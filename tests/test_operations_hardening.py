from __future__ import annotations

import unittest
import tempfile
import gc
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

import api
from mt5_connection import MT5ConnectionManager


class OperationsHardeningTests(unittest.TestCase):
    def test_account_limit_requires_numeric_login(self):
        with self.assertRaises(ValidationError):
            api.ExposureLimitRequest(
                scope_type="account",
                scope_value="not-a-login",
                metric="gross_lots",
                threshold=1,
            )

    def test_symbol_limit_rejects_unsupported_metric(self):
        with self.assertRaises(ValidationError):
            api.ExposureLimitRequest(
                scope_type="symbol",
                scope_value="EURUSD",
                metric="margin_level_below",
                threshold=100,
            )

    def test_notification_channel_validates_destination(self):
        with self.assertRaises(ValidationError):
            api.NotificationChannelRequest(
                name="Bad email",
                channel_type="email",
                destination="missing-at-sign",
            )
        with self.assertRaises(ValidationError):
            api.NotificationChannelRequest(
                name="Unsafe webhook",
                channel_type="webhook",
                destination="http://example.com/hook",
            )

    def test_mt5_user_cache_preserves_introducing_agent(self):
        manager = MT5ConnectionManager("server", 1, "secret")
        row = manager._normalize_user({"Login": 1001, "Agent": 77})
        self.assertEqual(row["Agent"], 77)

    def test_mt5_user_cache_never_synthesizes_equity(self):
        manager = MT5ConnectionManager("server", 1, "secret")
        row = manager._normalize_user(
            {"Login": 1001, "Balance": 1000, "Credit": 50, "Equity": 0, "Profit": 25}
        )
        self.assertEqual(row["Equity"], 0.0)

    def test_atomic_user_refresh_rejects_missing_account_metrics(self):
        manager = MT5ConnectionManager("server", 1, "secret")
        manager.fetch_all_users = lambda **_kwargs: [{"Login": 1001, "Name": "Client"}]
        manager.fetch_accounts_by_logins = lambda *_args, **_kwargs: {}
        with self.assertRaisesRegex(RuntimeError, "account snapshot is incomplete"):
            manager._build_users_cache(strict=True)

    def test_atomic_refresh_does_not_deepcopy_the_previous_generation(self):
        manager = MT5ConnectionManager("server", 1, "secret")
        old_user = {"Login": 1001, "Group": "real\\old", "Balance": 10.0}
        manager.cache["users"] = [old_user]
        manager.cache["users_by_login"] = {1001: old_user}
        manager.cache["positions"] = [{"Login": 1001, "Profit": 1.0, "Swap": 0.0}]
        manager.cache["positions_by_login"] = {1001: manager.cache["positions"]}
        new_user = {"Login": 2002, "Group": "real\\new", "Balance": 20.0}
        manager._build_users_cache = lambda **_kwargs: ([new_user], {2002: new_user})
        manager._build_positions_cache = lambda *_args, **_kwargs: ([], {})
        manager.call = lambda *_args, **_kwargs: 0

        with patch("mt5_connection.copy.deepcopy", side_effect=MemoryError("scale guard")):
            manager._refresh_core_cache_atomically(
                refresh_users=True,
                refresh_positions=True,
                refresh_groups=False,
            )

        self.assertEqual(manager.cache["users"], [new_user])
        self.assertEqual(manager.all_logins, [2002])

    def test_book_groups_cannot_overlap(self):
        with self.assertRaises(ValidationError):
            api.BookGroupSettings(a_book=["real\\shared"], b_book=["real\\shared"])

    def test_book_group_settings_round_trip_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "books.db"
            request = SimpleNamespace(state=SimpleNamespace(user={"username": "admin", "is_admin": 1}))
            with patch.object(api, "DB_PATH", str(database)):
                api.init_db()
                saved = api.update_book_group_settings(
                    api.BookGroupSettings(
                        a_book=["real\\a"],
                        b_book=["real\\b"],
                        cent=["cent\\usd"],
                        hybrid=["hybrid\\main"],
                    ),
                    request,
                )
                loaded = api.get_book_group_settings()
            gc.collect()

        self.assertEqual(saved, loaded)
        self.assertEqual(loaded["a_book"], ["real\\a"])
        self.assertEqual(loaded["all_groups"], "dynamic")


if __name__ == "__main__":
    unittest.main()
