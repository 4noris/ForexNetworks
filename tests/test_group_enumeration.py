from __future__ import annotations

import copy

import pytest

import api
import mt5_connection
from mt5_connection import MT5ConnectionManager


class IndexedGroupsAPI:
    def __init__(self, count: int):
        self.groups = [f"real\\branch-{index:03d}" for index in range(count)]

    def GroupTotal(self):
        return len(self.groups)

    def GroupNext(self, index):
        return {"Group": self.groups[index]}

    def GroupRequestArray(self, _mask="*"):
        # Reproduce wrappers that return only the first page.
        return [{"Group": group} for group in self.groups[:99]]


class CurrentGroups:
    def __init__(self, groups):
        self.groups = groups

    def GroupGet(self):
        return [{"Group": group} for group in self.groups[:99]]


class IndexedGroupsWithCappedCurrentAPI(IndexedGroupsAPI):
    def ManagerCurrent(self):
        return CurrentGroups(self.groups)


def test_request_groups_enumerates_beyond_first_99_records():
    groups, error = api.request_groups(IndexedGroupsAPI(157))

    assert error is None
    assert len(groups) == 157
    assert groups[-1] == "real\\branch-156"


def test_indexed_catalog_is_not_truncated_by_capped_manager_current_groups():
    groups, error = api.request_groups(IndexedGroupsWithCappedCurrentAPI(158))

    assert error is None
    assert len(groups) == 158
    assert groups[-1] == "real\\branch-157"


def test_persistent_manager_caches_configured_empty_groups():
    manager = MT5ConnectionManager("server", 1, "secret")
    manager.api = IndexedGroupsAPI(3)
    manager.cache["users"] = [{"Login": 1001, "Group": "real\\branch-000"}]

    manager._refresh_group_cache()
    snapshot = manager.snapshot()

    assert snapshot.groups == [
        "real\\branch-000", "real\\branch-001", "real\\branch-002",
    ]
    assert manager.group_cache_view() == snapshot.groups


def test_group_refresh_preserves_last_catalog_during_transient_failure():
    class FailingGroupsAPI:
        def GroupTotal(self):
            raise RuntimeError("temporary manager failure")

    manager = MT5ConnectionManager("server", 1, "secret")
    manager.cache["groups"] = ["existing\\group"]
    manager.api = FailingGroupsAPI()

    manager._refresh_group_cache()

    assert manager.group_cache_view() == ["existing\\group"]


def test_partial_indexed_group_response_never_replaces_complete_catalog():
    class PartiallyFailingGroupsAPI:
        def GroupTotal(self):
            return 3

        def GroupNext(self, index):
            if index == 1:
                raise RuntimeError("temporary manager failure")
            return {"Group": f"new\\group-{index}"}

    manager = MT5ConnectionManager("server", 1, "secret")
    manager.cache["groups"] = ["existing\\group"]
    manager.api = PartiallyFailingGroupsAPI()

    manager._refresh_group_cache()

    assert manager.group_cache_view() == ["existing\\group"]


def test_group_endpoint_never_opens_a_second_manager_session_while_cache_warms(monkeypatch):
    class PersistentManager:
        is_connected = True
        server = "server:443"
        manager_login = 1

        @staticmethod
        def live_risk_cache_view():
            return {"groups": [], "users": [], "last_update": 0.0}

    monkeypatch.setattr(mt5_connection, "get_manager", lambda: PersistentManager())
    monkeypatch.setattr(
        api,
        "configured_manager_credentials",
        lambda: ("server:443", 1, "secret"),
    )
    monkeypatch.setattr(
        api,
        "manager_session",
        lambda *_args, **_kwargs: pytest.fail("group endpoint opened a second Manager session"),
    )

    result = api.fetch_groups(api.MT5Credentials())

    assert result["groups"] == []
    assert result["source"] == "manager_cache_initializing"


def test_manager_generation_rolls_back_when_later_sdk_fetch_fails(monkeypatch):
    manager = MT5ConnectionManager("server", 1, "secret")
    manager.cache.update({
        "users": [{"Login": 10, "Group": "old\\group", "Balance": 100.0}],
        "users_by_login": {10: {"Login": 10, "Group": "old\\group", "Balance": 100.0}},
        "positions": [{"Login": 10, "Ticket": 1}],
        "positions_by_login": {10: [{"Login": 10, "Ticket": 1}]},
        "summary_rows": [{"Symbol": "OLD"}],
        "exposure_rows": [{"Symbol": "OLD"}],
        "groups": ["old\\group"],
        "metrics": {"total_users": 1, "total_positions": 1},
        "last_update": 123.0,
        "status": "connected",
        "errors": [],
        "refresh_phase": "published",
    })
    before = copy.deepcopy(manager.cache)
    new_user = {"Login": 20, "Group": "new\\group", "Balance": 200.0}
    monkeypatch.setattr(manager, "_build_users_cache", lambda **_kwargs: ([new_user], {20: new_user}))

    def fail_positions(*_args, **_kwargs):
        raise RuntimeError("temporary position SDK failure")

    monkeypatch.setattr(manager, "_build_positions_cache", fail_positions)

    with pytest.raises(RuntimeError, match="temporary position SDK failure"):
        manager._refresh_core_cache_atomically(
            refresh_users=True,
            refresh_positions=True,
            refresh_groups=False,
        )

    assert manager.cache == before


def test_manager_generation_publishes_groups_clients_and_metrics_together(monkeypatch):
    manager = MT5ConnectionManager("server", 1, "secret")
    user = {
        "Login": 20, "Group": "new\\group", "Enabled": True, "Balance": 200.0,
        "Equity": 210.0, "Margin": 10.0, "Free Margin": 200.0, "Floating PnL": 10.0,
    }
    position = {"Login": 20, "Ticket": 2}
    monkeypatch.setattr(manager, "_build_users_cache", lambda **_kwargs: ([user], {20: user}))
    monkeypatch.setattr(manager, "_build_positions_cache", lambda *_args, **_kwargs: ([position], {20: [position]}))
    monkeypatch.setattr(manager, "fetch_summary_rows", lambda **_kwargs: [])
    monkeypatch.setattr(manager, "fetch_exposure_rows", lambda **_kwargs: [])
    monkeypatch.setattr(manager, "_fetch_group_catalog", lambda **_kwargs: ["empty\\group"])

    manager._refresh_core_cache_atomically(
        refresh_users=True,
        refresh_positions=True,
        refresh_groups=True,
    )
    snapshot = manager.snapshot()

    assert snapshot.users == [user]
    assert snapshot.positions == [position]
    assert snapshot.groups == ["empty\\group", "new\\group"]
    assert snapshot.metrics["total_users"] == 1
    assert snapshot.metrics["total_positions"] == 1
    assert snapshot.last_update > 0
    assert snapshot.refresh_phase == "published"


def test_position_only_generation_updates_live_floating_pnl(monkeypatch):
    manager = MT5ConnectionManager("server", 1, "secret")
    user = {
        "Login": 20, "Group": "real", "Enabled": True, "Balance": 200.0,
        "Equity": 210.0, "Margin": 10.0, "Free Margin": 200.0, "Floating PnL": 10.0,
    }
    manager.cache["users"] = [copy.deepcopy(user)]
    manager.cache["users_by_login"] = {20: copy.deepcopy(user)}
    position = {"Login": 20, "Ticket": 2, "Profit": 18.5, "Swap": -0.5}
    monkeypatch.setattr(manager, "_build_positions_cache", lambda *_args, **_kwargs: ([position], {20: [position]}))
    monkeypatch.setattr(manager, "fetch_summary_rows", lambda **_kwargs: [])
    monkeypatch.setattr(manager, "fetch_exposure_rows", lambda **_kwargs: [])

    manager._refresh_core_cache_atomically(
        refresh_users=False,
        refresh_positions=True,
        refresh_groups=False,
    )
    snapshot = manager.snapshot()

    assert snapshot.users[0]["Floating PnL"] == 10.0
    assert snapshot.users[0]["Live Floating PnL"] == 18.0
    assert snapshot.metrics["total_floating"] == 18.0
