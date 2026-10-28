from datetime import datetime, timedelta, timezone

import api


def _payload():
    return {
        "accounts": [{
            "login": 1001,
            "severity": "critical",
            "signals": ["Low margin", "Large loss"],
        }]
    }


def test_live_risk_notice_hides_same_activity_for_48_hours(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "notices.db")
    api.init_db()
    first = api._apply_live_risk_notices(_payload())
    key = first["accounts"][0]["alert_key"]
    with api._conn() as con:
        con.execute(
            "INSERT INTO alert_acknowledgements(alert_key,actor) VALUES (?,?)",
            (key, "dealer"),
        )

    noticed = api._apply_live_risk_notices(_payload())

    assert noticed["accounts"][0]["noticed"] is True
    assert noticed["noticed_hidden"] == 1


def test_live_risk_notice_expires_after_48_hours(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "expired-notices.db")
    api.init_db()
    first = api._apply_live_risk_notices(_payload())
    key = first["accounts"][0]["alert_key"]
    expired = datetime.now(timezone.utc) - timedelta(hours=49)
    with api._conn() as con:
        con.execute(
            """INSERT INTO alert_acknowledgements(alert_key,actor,acknowledged_at)
               VALUES (?,?,?)""",
            (key, "dealer", expired.strftime("%Y-%m-%d %H:%M:%S")),
        )

    visible = api._apply_live_risk_notices(_payload())

    assert not visible["accounts"][0].get("noticed", False)
    assert visible["noticed_hidden"] == 0
