from types import SimpleNamespace

import api


def test_book_shift_captures_equity_from_changed_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "book-shifts.db")
    api.init_db()
    with api._conn() as con:
        con.execute(
            "UPDATE book_group_settings SET a_book_json=?, b_book_json=? WHERE id=1",
            ('["real/a"]', '["real/b"]'),
        )

    before = SimpleNamespace(
        last_update=100.0,
        users=[{"Login": 100112, "Name": "Client", "Group": "real/a", "Equity": 1200.0, "Live Floating PnL": -20.0}],
    )
    after = SimpleNamespace(
        last_update=105.0,
        users=[{"Login": 100112, "Name": "Client", "Group": "real/b", "Equity": 1175.25, "Live Floating PnL": -24.75}],
    )

    assert api._capture_book_shifts(before, manager_login=1001) == 0
    assert api._capture_book_shifts(after, manager_login=1001) == 1
    response = api.Response()
    result = api.get_book_shift_events(response, 100)
    assert response.headers["cache-control"] == "no-store, max-age=0"
    event = result["items"][0]
    assert event["manager_login"] == 1001
    assert event["login"] == 100112
    assert event["from_group"] == "real/a"
    assert event["to_group"] == "real/b"
    assert event["from_book"] == "a_book"
    assert event["to_book"] == "b_book"
    assert event["equity_at_shift"] == 1175.25
    assert event["floating_pnl_at_shift"] == -24.75
    assert event["from_book_correction"] == -24.75
    assert event["to_book_correction"] == 24.75


def test_clear_shift_history_preserves_membership_baseline_and_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "book-shifts-clear.db")
    api.init_db()
    before = SimpleNamespace(last_update=100.0, users=[{"Login": 10, "Group": "real/a", "Equity": 1000.0}])
    after = SimpleNamespace(last_update=101.0, users=[{"Login": 10, "Group": "real/b", "Equity": 1000.0}])
    api._capture_book_shifts(before, manager_login=1)
    assert api._capture_book_shifts(after, manager_login=1) == 1
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "admin", "is_admin": True}))
    result = api.clear_book_shift_history(api.ClearBookShiftHistoryRequest(confirmation="CLEAR BOOK SHIFTS"), request)
    assert result == {"ok": True, "deleted_rows": 1}
    with api._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM book_shift_events").fetchone()[0] == 0
        assert con.execute("SELECT account_group FROM book_membership_state WHERE login=10").fetchone()[0] == "real/b"
        assert con.execute("SELECT COUNT(*) FROM audit_log WHERE action='book_shifts.cleared'").fetchone()[0] == 1
    assert api._capture_book_shifts(after, manager_login=1) == 0
