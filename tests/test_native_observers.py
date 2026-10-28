import threading
from types import SimpleNamespace

from services.native_observers import NativeMembershipCursor, online_account_identities


def manager():
    return SimpleNamespace(cache_lock=threading.RLock(),cache={"status":"connected","last_update":1,
        "native_epoch":"test-only","native_profile_revision":1,"errors":[],
        "users_by_login":{7:{"Login":7,"Name":"Test","Group":"a","Equity":100}}})


def test_financial_ticks_do_not_rescan_membership_or_rewrite_database_baseline():
    source = manager()
    cursor = NativeMembershipCursor()
    first = cursor.prepare(source,{"a_book":["a"]})
    assert len(first.snapshot.users)==1
    cursor.commit(first)
    assert cursor.rows == {7:("Test","a")}

    class NoIteration(dict):
        def items(self):
            raise AssertionError("Unchanged profile revision must not scan accounts")

        def values(self):
            raise AssertionError("Unchanged profile revision must not copy accounts")

    source.cache["users_by_login"] = NoIteration({7:dict(first.rows[7],Equity=110)})
    source.cache["last_update"] = 2
    assert cursor.prepare(source,{"a_book":["a"]}) is None


def test_membership_change_uses_equity_from_current_atomic_generation_and_retries():
    source = manager()
    cursor = NativeMembershipCursor()
    cursor.commit(cursor.prepare(source,{}))
    source.cache.update(native_profile_revision=2,last_update=20,
                        users_by_login={7:dict(source.cache["users_by_login"][7],Group="b",Equity=75)})
    batch = cursor.prepare(source,{})
    assert batch.snapshot.users[0]["Equity"]==75
    assert batch.snapshot.last_update==20
    # No commit simulates persistence failure; the same change must be retried.
    assert cursor.prepare(source,{}).snapshot.users==batch.snapshot.users
    cursor.commit(batch)
    assert cursor.prepare(source,{}) is None


def test_book_configuration_and_source_epoch_changes_rebuild_membership():
    source = manager()
    cursor = NativeMembershipCursor()
    cursor.commit(cursor.prepare(source,{"a_book":["a"]}))
    changed = cursor.prepare(source,{"b_book":["a"]})
    assert len(changed.snapshot.users)==1
    cursor.commit(changed)
    source.cache["native_epoch"]="new-test-source"
    assert len(cursor.prepare(source,{"b_book":["a"]}).snapshot.users)==1
    source.cache["status"]="disconnected"
    assert cursor.prepare(source,{}) is None


def test_online_identity_reads_only_online_logins_and_never_copies_positions():
    source = manager()
    class NoIteration(dict):
        def __iter__(self):
            raise AssertionError("CID observer must use the login index")
    source.cache["users_by_login"]=NoIteration(source.cache["users_by_login"])
    identities=online_account_identities(source,{7,999})
    assert identities=={7:{"Name":"Test","Group":"a"}}
    identities[7]["Name"]="Changed"
    assert source.cache["users_by_login"][7]["Name"]=="Test"
