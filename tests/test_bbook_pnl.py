from types import SimpleNamespace

import api
from fastapi import Response


def test_bbook_snapshot_values_uses_one_atomic_generation():
    snapshot = SimpleNamespace(users=[
        {"Login": 1, "Group": "real\\b", "Floating PnL": 10.0, "Live Floating PnL": 12.5},
        {"Login": 2, "Group": "real\\b", "Floating PnL": -2.0},
        {"Login": 3, "Group": "real\\a", "Live Floating PnL": 100.0},
    ])

    floating, count = api._bbook_snapshot_values(snapshot, ["real\\b"])

    assert floating == 10.5
    assert count == 2


def test_bbook_dashboard_returns_daily_delta_and_total(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "bbook.db")
    api.init_db()
    with api._conn() as con:
        con.executemany(
            """INSERT INTO book_pnl_snapshots
               (book,captured_at,source_updated_at,floating,closed_swaps_comm,account_count)
               VALUES (?,?,?,?,?,?)""",
            [
                ("b_book", "2026-08-01T00:00:00+00:00", 1, -6900, -125, 10),
                ("b_book", "2026-08-01T23:59:00+00:00", 2, -6514.18, -125, 11),
                ("a_book", "2026-08-01T23:59:00+00:00", 2, 400, 75, 5),
            ],
        )

    payload = api._book_dashboard_payload("b_book", api.date(2026, 8, 1), api.date(2026, 8, 1))

    assert payload["daily"][0]["floating_delta"] == 385.82
    assert payload["daily"][0]["client_pnl"] == 260.82
    assert payload["daily"][0]["broker_pnl"] == -260.82
    assert payload["daily"][0]["total_bbook_pnl"] == -260.82
    assert payload["daily"][0]["account_count"] == 11
    assert payload["book_label"] == "B Book"

    a_book = api._book_dashboard_payload("a_book", api.date(2026, 8, 1), api.date(2026, 8, 1))
    assert a_book["book_label"] == "A Book"
    assert a_book["daily"][0]["account_count"] == 5


def test_historical_backfill_persists_each_selected_day(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "book-history.db")
    api.init_db()
    monkeypatch.setattr(api, "get_book_group_settings", lambda: {"a_book": [], "b_book": ["real\\b"]})
    day0 = int(api.datetime(2026, 7, 31, 23, tzinfo=api.timezone.utc).timestamp())
    day1 = int(api.datetime(2026, 8, 1, 23, tzinfo=api.timezone.utc).timestamp())
    day2 = int(api.datetime(2026, 8, 2, 23, tzinfo=api.timezone.utc).timestamp())
    daily = [
        {"Login": 1, "Datetime": day0, "Equity": 900, "Balance": 1000, "Credit": 0},
        {"Login": 1, "Datetime": day1, "Equity": 920, "Balance": 1000, "Credit": 0},
        {"Login": 1, "Datetime": day2, "Equity": 950, "Balance": 1000, "Credit": 0},
    ]
    deals = [{
        "Deal": 10, "Login": 1, "Action": api.DEAL_BUY, "Entry": api.ENTRY_OUT,
        "Time": day1, "Volume": 10000, "Profit": -10, "Commission": -1,
    }, {
        "Deal": 11, "Login": 1, "Action": api.DEAL_BUY, "Entry": 0,
        "Time": day1, "Volume": 10000, "Profit": 0, "Commission": -2,
    }]

    class Session:
        pass

    class Context:
        def __enter__(self):
            return Session()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(api, "yielding_history_manager_session", Context)
    monkeypatch.setattr(api, "request_daily_by_group", lambda *_args: (daily, None))
    monkeypatch.setattr(api, "request_deals_by_group", lambda *_args: (deals, None))

    stored = api._backfill_book_pnl_history(
        "b_book", api.date(2026, 8, 1), api.date(2026, 8, 2)
    )
    payload = api._book_dashboard_payload(
        "b_book", api.date(2026, 8, 1), api.date(2026, 8, 2)
    )

    assert stored == 2
    rows = list(reversed(payload["daily"]))
    assert [row["day"] for row in rows] == ["2026-08-01", "2026-08-02"]
    assert rows[0]["opening_floating"] == -100
    assert rows[0]["closing_floating"] == -80
    assert rows[0]["floating_delta"] == 20
    assert rows[0]["closed_profit"] == -10
    assert rows[0]["commissions"] == -3
    assert rows[0]["closed_swaps_comm"] == -13
    assert rows[0]["client_pnl"] == 7
    assert rows[0]["total_bbook_pnl"] == -7


def test_book_backfill_does_not_turn_missing_daily_equity_into_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "incomplete-book.db")
    api.init_db()
    monkeypatch.setattr(api, "get_book_group_settings", lambda: {"b_book": ["real\\b"]})
    day0 = int(api.datetime(2026, 7, 31, 23, tzinfo=api.timezone.utc).timestamp())
    day1 = int(api.datetime(2026, 8, 1, 23, tzinfo=api.timezone.utc).timestamp())

    class Session:
        pass

    class Context:
        def __enter__(self):
            return Session()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(api, "yielding_history_manager_session", Context)
    monkeypatch.setattr(api, "request_daily_by_group", lambda *_args: ([
        {"Login": 1, "Datetime": day0, "Equity": 900, "Balance": 1000, "Credit": 0},
        {"Login": 1, "Datetime": day1, "Balance": 1000, "Credit": 0},
    ], None))
    monkeypatch.setattr(api, "request_deals_by_group", lambda *_args: ([], None))

    assert api._backfill_book_pnl_history("b_book", api.date(2026, 8, 1), api.date(2026, 8, 1)) == 0
    assert api._book_dashboard_payload("b_book", api.date(2026, 8, 1), api.date(2026, 8, 1))["daily"] == []


def test_book_backfill_does_not_turn_absent_daily_rows_into_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "absent-daily.db")
    api.init_db()
    monkeypatch.setattr(api, "get_book_group_settings", lambda: {"b_book": ["real\\b"]})

    class Context:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(api, "yielding_history_manager_session", Context)
    monkeypatch.setattr(api, "request_daily_by_group", lambda *_args: ([], None))
    monkeypatch.setattr(api, "request_deals_by_group", lambda *_args: ([], None))

    assert api._backfill_book_pnl_history("b_book", api.date(2026, 8, 1), api.date(2026, 8, 1)) == 0
    assert api._book_dashboard_payload("b_book", api.date(2026, 8, 1), api.date(2026, 8, 1))["daily"] == []


def test_configured_book_excludes_zero_account_snapshots(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "empty-scope.db")
    api.init_db()
    monkeypatch.setattr(api, "get_book_group_settings", lambda: {"b_book": ["real\\b"]})
    with api._conn() as con:
        con.execute(
            """INSERT INTO book_pnl_snapshots
               (book,captured_at,source_updated_at,floating,closed_swaps_comm,account_count)
               VALUES (?,?,?,?,?,?)""",
            ("b_book", "2026-08-01T23:00:00+00:00", 1, 0, 0, 0),
        )
        con.execute(
            """INSERT INTO book_pnl_daily (book,report_day,account_count)
               VALUES (?,?,?)""",
            ("b_book", "2026-08-01", 0),
        )

    payload = api._book_dashboard_payload("b_book", api.date(2026, 8, 1), api.date(2026, 8, 1))
    assert payload["daily"] == []
    assert payload["totals"] == {}


def test_book_snapshot_does_not_publish_stale_closed_pnl_when_history_fails(monkeypatch, tmp_path):
    import mt5_connection

    monkeypatch.setattr(api, "DB_PATH", tmp_path / "failed-history.db")
    api.init_db()
    snapshot = SimpleNamespace(last_update=1, users=[{
        "Login": 1001, "Group": "real\\b", "Balance": 1000,
        "Equity": 1020, "Live Floating PnL": 20,
    }])
    monkeypatch.setattr(mt5_connection, "get_manager", lambda: SimpleNamespace(
        is_connected=True, snapshot=lambda: snapshot,
    ))
    monkeypatch.setattr(api, "get_book_group_settings", lambda: {"b_book": ["real\\b"]})

    class Context:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(api, "yielding_history_manager_session", Context)
    monkeypatch.setattr(api, "request_deals_by_group", lambda *_args: ([], "history denied"))

    assert api._capture_book_pnl_snapshots() == 0
    with api._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM book_pnl_snapshots").fetchone()[0] == 0


def test_book_range_total_requires_every_selected_day(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "partial-range.db")
    api.init_db()
    monkeypatch.setattr(api, "get_book_group_settings", lambda: {"b_book": []})
    with api._conn() as con:
        con.executemany(
            """INSERT INTO book_pnl_snapshots
               (book,captured_at,source_updated_at,floating,closed_swaps_comm,account_count)
               VALUES (?,?,?,?,?,?)""",
            [("b_book", "2026-08-01T00:00:00+00:00", 1, 0, -10, 1),
             ("b_book", "2026-08-01T23:00:00+00:00", 2, 0, -10, 1)],
        )

    payload = api.get_book_pnl(Response(), "b_book", api.date(2026, 8, 1), api.date(2026, 8, 2))
    assert payload["coverage"]["complete"] is False
    assert payload["totals"]["total_book_pnl"] is None
    assert payload["available_days_subtotal"]["total_book_pnl"] == 10


def test_a_book_uses_verified_deal_charges_when_daily_snapshot_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "a-book-deal-days.db")
    api.init_db()
    monkeypatch.setattr(api, "get_book_group_settings", lambda: {"a_book": ["real\\a"]})
    deal_time = int(api.datetime(2026, 8, 2, 12, tzinfo=api.timezone.utc).timestamp())

    class Context:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(api, "yielding_history_manager_session", Context)
    monkeypatch.setattr(api, "request_daily_by_group", lambda *_args: ([], None))
    monkeypatch.setattr(api, "request_deals_by_group", lambda *_args: ([{
        "Deal": 10, "Login": 1, "Action": api.DEAL_BUY,
        "Entry": api.ENTRY_OUT, "Time": deal_time, "Volume": 10000,
        "Profit": 100, "Commission": -5,
    }], None))

    assert api._backfill_book_pnl_history("a_book", api.date(2026, 8, 1), api.date(2026, 8, 2)) == 2
    payload = api.get_book_pnl(Response(), "a_book", api.date(2026, 8, 1), api.date(2026, 8, 2))
    assert payload["coverage"]["complete"] is True
    assert payload["totals"]["total_book_pnl"] is None
    assert payload["posted_client_charge_offset"] == 5
    assert payload["daily"][0]["broker_pnl"] is None
    assert payload["totals"]["broker_commission"] == 5
    assert payload["totals"]["broker_swap"] == 0
    assert payload["totals"]["client_pnl"] is None
    assert payload["daily"][0]["opening_floating"] is None


def test_completed_day_uses_verified_history_over_older_live_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "a-book-priority.db")
    api.init_db()
    with api._conn() as con:
        con.execute(
            """INSERT INTO book_pnl_snapshots
               (book,captured_at,source_updated_at,swaps,commissions,account_count)
               VALUES (?,?,?,?,?,?)""",
            ("a_book", "2026-08-02T12:00:00+00:00", 1, 0, -1, 1),
        )
        con.execute(
            """INSERT INTO book_pnl_daily
               (book,report_day,swaps,commissions,total_book_pnl,source_status)
               VALUES (?,?,?,?,?,?)""",
            ("a_book", "2026-08-02", 0, -5, 5, "charges_only"),
        )

    payload = api._book_dashboard_payload("a_book", api.date(2026, 8, 2), api.date(2026, 8, 2))
    assert len(payload["daily"]) == 1
    assert payload["daily"][0]["source"] == "verified_deals_only"
    assert payload["totals"]["total_book_pnl"] == 5


def test_book_performance_reconciles_real_deal_fields_and_deduplicates(monkeypatch):
    stamp = int(api.datetime(2026, 10, 3, 12, tzinfo=api.timezone.utc).timestamp())
    opening = {"Deal": 1, "Login": 10, "Time": stamp, "Action": api.DEAL_BUY,
               "Entry": 0, "Volume": 10000, "Profit": 0, "Commission": -2}
    closing = {"Deal": 2, "Login": 10, "Time": stamp + 1, "Action": api.DEAL_SELL,
               "Entry": api.ENTRY_OUT, "Volume": 10000, "Profit": -100,
               "Commission": -3, "Fee": -1, "Storage": -4}
    dedicated = {"Deal": 3, "Login": 10, "Time": stamp + 2,
                 "Action": api.DEAL_COMMISSION, "Profit": -5}
    monkeypatch.setattr(api.brokerage_pnl, "request_group_currency", lambda *_: ("USD", None))
    monkeypatch.setattr(api, "request_deals_by_group", lambda _api, group, *_:
                        ([opening, closing, dedicated] if group == "a" else [dict(closing)], None))

    payload = api._book_performance_from_deals(object(), "a_book", ["a", "b"],
                                                api.date(2026, 10, 2), api.date(2026, 10, 9))
    assert payload["complete"] is True
    usd = payload["currencies"][0]
    assert usd["trade_profit"] == -100
    assert usd["commission"] == -10
    assert usd["fee"] == -1
    assert usd["swap"] == -4
    assert usd["client_deal_result"] == -115
    assert usd["executions"] == 2
    assert usd["closed_deals"] == 1
    assert usd["broker_net_pnl"] is None
    assert usd["daily"][-1]["cumulative_result"] == -115
    assert sum(row["client_deal_result"] for row in payload["groups"]) == -115


def test_book_performance_keeps_failed_groups_and_currencies_separate(monkeypatch):
    stamp = int(api.datetime(2026, 10, 3, 12, tzinfo=api.timezone.utc).timestamp())
    monkeypatch.setattr(api.brokerage_pnl, "request_group_currency",
                        lambda _api, group: ({"usd": "USD", "eur": "EUR", "bad": "USD"}[group], None))
    monkeypatch.setattr(api, "request_deals_by_group", lambda _api, group, *_: (
        ([], "permission denied") if group == "bad" else
        ([{"Deal": {"usd": 10, "eur": 11}[group], "Login": 1, "Time": stamp,
           "Action": api.DEAL_BUY, "Entry": api.ENTRY_OUT,
           "Profit": 10, "Volume": 10000}], None)))

    payload = api._book_performance_from_deals(object(), "b_book", ["usd", "eur", "bad"],
                                                api.date(2026, 10, 2), api.date(2026, 10, 9))
    assert payload["complete"] is False
    assert payload["groups_checked"] == 2
    assert sorted(item["currency"] for item in payload["currencies"]) == ["EUR", "USD"]
    assert all(item["client_deal_result"] == 10 for item in payload["currencies"])
    assert any("permission denied" in warning for warning in payload["warnings"])


def test_empty_manager_deal_history_is_an_empty_array(monkeypatch):
    class API:
        def DealRequestByGroup(self, *_args):
            return False

    monkeypatch.setattr(api.brokerage_pnl, "_last_error_code", lambda: 13)
    assert api.request_deals_by_group(API(), "empty", 1, 2) == ([], None)


def test_cent_book_values_are_converted_from_usc_to_usd(monkeypatch):
    monkeypatch.setattr(api, "CENT_ACCOUNT_DIVISOR", 100.0)
    snapshot = SimpleNamespace(users=[{
        "Login": 1, "Group": "cent\\live", "Balance": 100_000,
        "Equity": 105_000, "Live Floating PnL": 5_000,
    }])

    values = api._book_live_values(snapshot, ["cent\\live"], "cent")

    assert values == {
        "floating": 50.0, "balance": 1000.0, "equity": 1050.0,
        "account_count": 1,
    }


def test_book_perspectives_keep_client_and_broker_signs_separate():
    assert api._book_broker_pnl("b_book", 125, -10, -5) == -125
    assert api._book_broker_pnl("a_book", 125, -10, -5) == 15
    assert api._book_broker_pnl("hybrid", 125, -10, -5) is None


def test_a_book_dashboard_uses_posted_charges_not_client_trading_result(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "a-book.db")
    api.init_db()
    with api._conn() as con:
        con.executemany(
            """INSERT INTO book_pnl_snapshots
               (book,captured_at,source_updated_at,floating,closed_profit,swaps,commissions,closed_swaps_comm)
               VALUES (?,?,?,?,?,?,?,?)""",
            [("a_book", "2026-08-01T00:00:00+00:00", 1, 0, 0, 0, 0, 0),
             ("a_book", "2026-08-01T23:00:00+00:00", 2, 100, 50, -10, -5, 35)],
        )
    row = api._book_dashboard_payload("a_book", api.date(2026, 8, 1), api.date(2026, 8, 1))["daily"][0]
    assert row["client_pnl"] == 135
    assert row["broker_pnl"] == row["total_book_pnl"] == 15


def test_shift_correction_offsets_incoming_floating(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "book-shift.db")
    api.init_db()
    with api._conn() as con:
        con.execute(
            """INSERT INTO book_shift_events
               (manager_login,login,from_group,to_group,from_book,to_book,
                equity_at_shift,floating_pnl_at_shift,source_updated_at,detected_at)
               VALUES (1,1001,'a','b','a_book','b_book',1000,-75,1,
                       '2026-08-01T12:00:00+00:00')"""
        )
        con.executemany(
            """INSERT INTO book_pnl_snapshots
               (book,captured_at,source_updated_at,floating,closed_swaps_comm)
               VALUES (?,?,?,?,?)""",
            [
                ("b_book", "2026-08-01T00:00:00+00:00", 1, -100, 0),
                ("b_book", "2026-08-01T23:00:00+00:00", 2, -175, 0),
            ],
        )

    payload = api._book_dashboard_payload(
        "b_book", api.date(2026, 8, 1), api.date(2026, 8, 1)
    )

    row = payload["daily"][0]
    assert row["floating_delta"] == -75
    assert row["shift_corrections"] == 75
    assert row["total_bbook_pnl"] == 0
