import pytest

import api


TEST_RESET_PASSWORD = "fixture-reset-password-only"


def test_database_reset_clears_generated_data_and_preserves_users(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "reset.db")
    monkeypatch.setattr(api, "_risk_admin_actor", lambda request: "admin")
    monkeypatch.setenv("RMS_RESET_PASSWORD", TEST_RESET_PASSWORD)
    api.init_db()
    with api._conn() as con:
        con.execute(
            "INSERT INTO cid_observations (login,computer_id) VALUES (?,?)",
            (900301, "device-a"),
        )
        con.execute(
            "INSERT INTO exposure_limits (scope_type,metric,threshold) VALUES (?,?,?)",
            ("global", "gross_lots", 10),
        )

    result = api.reset_application_database(
        api.DatabaseResetRequest(
            password=TEST_RESET_PASSWORD,
            confirmation="DELETE ALL DATA",
        ),
        request=None,
    )

    assert result["ok"] is True
    with api._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM app_users").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM cid_observations").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM exposure_limits").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM book_group_settings").fetchone()[0] == 1
        audit = con.execute(
            "SELECT action FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit[0] == "database.reset"


def test_database_reset_rejects_wrong_password_without_deleting(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "reset-denied.db")
    monkeypatch.setattr(api, "_risk_admin_actor", lambda request: "admin")
    monkeypatch.setenv("RMS_RESET_PASSWORD", TEST_RESET_PASSWORD)
    api.init_db()
    with api._conn() as con:
        con.execute(
            "INSERT INTO cid_observations (login,computer_id) VALUES (?,?)",
            (900301, "device-a"),
        )

    with pytest.raises(api.HTTPException) as exc:
        api.reset_application_database(
            api.DatabaseResetRequest(
                password="wrong",
                confirmation="DELETE ALL DATA",
            ),
            request=None,
        )

    assert exc.value.status_code == 403
    with api._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM cid_observations").fetchone()[0] == 1


@pytest.mark.parametrize("configured", [None, "", "   "])
def test_database_reset_requires_explicit_password_without_mutation(monkeypatch, tmp_path, configured):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "reset-unconfigured.db")
    monkeypatch.setattr(api, "_risk_admin_actor", lambda request: "fixture-admin")
    if configured is None:
        monkeypatch.delenv("RMS_RESET_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("RMS_RESET_PASSWORD", configured)
    api.init_db()
    with api._conn() as con:
        con.execute("INSERT INTO cid_observations (login,computer_id) VALUES (?,?)", (900301, "fixture-device"))
    with pytest.raises(api.HTTPException) as exc:
        api.reset_application_database(api.DatabaseResetRequest(password="fixture-rejected-password", confirmation="DELETE ALL DATA"), request=None)
    assert exc.value.status_code == 503
    with api._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM cid_observations").fetchone()[0] == 1
