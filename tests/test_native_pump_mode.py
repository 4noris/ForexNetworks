from types import SimpleNamespace

import pytest
import mt5_connection


@pytest.mark.parametrize("native",[True,False])
def test_report_connection_does_not_duplicate_native_live_book(monkeypatch,native):
    captured=[]
    class API:
        EnPumpModes=SimpleNamespace(PUMP_MODE_USERS=1,PUMP_MODE_GROUPS=2,PUMP_MODE_SYMBOLS=4,
                                    PUMP_MODE_TIME=8,PUMP_MODE_POSITIONS=16,PUMP_MODE_ORDERS=32)
        def Connect(self,*args):captured.append(args);return True
        def UserRequest(self,login):return {"Login":login}
    monkeypatch.setattr(mt5_connection.MT5Manager,"ManagerAPI",API)
    manager=mt5_connection.MT5ConnectionManager("test-only",1,"")
    manager.native_mode=native
    handle=manager._create_connected_api()
    assert captured[0][3]==(14 if native else 63)
    assert handle.UserRequest(7)=={"Login":7}
