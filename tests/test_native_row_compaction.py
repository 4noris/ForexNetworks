from mt5_connection import MT5ConnectionManager
from services.native_bridge import NativeProjection
from test_native_bridge import snapshot
from test_native_changes import change_frame


def test_verified_profile_account_and_published_rows_share_storage():
    manager=MT5ConnectionManager("test-only",1,"")
    state=NativeProjection()
    state.snapshot(snapshot())
    view=state.capture()
    view.publish(manager)
    view.compact_rows(state)
    row=manager.cache["users_by_login"][7]
    assert state.records["users"]["7"] is row
    assert state.records["accounts"]["7"] is row
    assert view.records["users"]["7"] is row
    assert view.records["accounts"]["7"] is row
    assert row["Equity"]==127


def test_compacted_account_cannot_override_new_profile_name_or_group():
    manager=MT5ConnectionManager("test-only",1,"")
    state=NativeProjection()
    source=snapshot()
    source["records"]["users"]["7"]["Name"]="Before"
    state.snapshot(source)
    first=state.capture()
    first.publish(manager)
    first.compact_rows(state)
    frame=change_frame(state)
    frame["updates"]={"users":{"7":dict(source["records"]["users"]["7"],Name="After",Group="new-group")}}
    state.apply_changes(frame)
    second=state.capture(first)
    second.publish(manager)
    row=manager.cache["users_by_login"][7]
    assert row["Name"]=="After"
    assert row["Group"]=="new-group"
    assert row["Equity"]==127
    assert first.published_users[0]["Name"]=="Before"


def test_compaction_does_not_overwrite_a_newer_financial_callback():
    manager=MT5ConnectionManager("test-only",1,"")
    state=NativeProjection()
    state.snapshot(snapshot())
    first=state.capture()
    first.publish(manager)
    frame=change_frame(state)
    state.apply_changes(frame)
    newest=state.records["accounts"]["7"]
    first.compact_rows(state)
    assert state.records["accounts"]["7"] is newest
    assert newest["Equity"]==321
    second=state.capture(first)
    second.publish(manager)
    assert manager.cache["users_by_login"][7]["Equity"]==321
