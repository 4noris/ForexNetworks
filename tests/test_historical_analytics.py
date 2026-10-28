from datetime import datetime, timezone
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from services.historical import HistoryStore
from services.history_worker import HistoryWorker, normalize


def value(row, key, default=None):
    return row.get(key,default)


class HistoricalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = HistoryStore(Path(self.temp.name)/"test.db")
        self.store.migrate("2026-10-01")
        self.filters = {"from_date":"2026-10-01","to_date":"2026-10-25"}
        self.start = int(datetime(2026,10,1,tzinfo=timezone.utc).timestamp())
        self.job = self.store.enqueue()

    def tearDown(self):
        self.temp.cleanup()

    def deal(self, ticket=1, profit="0.1", currency="USD", **extra):
        row = {"Deal":ticket,"Login":10,"Time":self.start+10,"Action":1,"Entry":1,"Symbol":"EURUSD",
               "Profit":profit,"Commission":"-0.02","Storage":"-0.01","Fee":"-0.01","Volume":10000,**extra}
        return normalize(row,"deal",{10:{"currency":currency}},"real",value)

    def save(self, rows):
        return self.store.save_batch(self.job,"real",self.start,self.start+86399,rows)

    def test_idempotency_corrections_and_decimal_totals(self):
        self.assertEqual(self.save([self.deal()])["inserted"],1)
        self.assertEqual(self.save([self.deal()])["skipped"],1)
        self.save([self.deal(2,"0.2")])
        report = self.store.report(self.filters)
        self.assertEqual(Decimal(report["totals"][0]["net"]),Decimal("0.22"))
        self.assertEqual(self.save([self.deal(1,"0.3")])["updated"],1)
        report = self.store.report(self.filters)
        self.assertEqual(Decimal(report["totals"][0]["net"]),Decimal("0.42"))
        self.assertEqual(report["totals"][0]["deals"],2)
        self.assertEqual(self.store.status()["discrepancies"],1)

    def test_currency_and_scope_separation(self):
        self.save([self.deal(1,currency="USD"),self.deal(2,currency="EUR")])
        self.assertEqual(len(self.store.report(self.filters)["totals"]),2)
        self.assertEqual(self.store.report({**self.filters,"group":"other"})["total"],0)
        self.assertEqual(self.store.report({**self.filters,"login":20})["total"],0)
        self.assertEqual(self.store.report({**self.filters,"symbol":"GBPUSD"})["total"],0)
        self.assertEqual(len(self.store.report({**self.filters,"currency":"EUR"})["totals"]),1)

    def test_missing_costs_are_unavailable_not_zero_or_partial(self):
        self.save([self.deal(1),self.deal(2,Fee=None)])
        self.assertIsNone(self.store.report(self.filters)["totals"][0]["net"])
        self.assertIsNone(self.store.report(self.filters)["rows"][0]["fee"])

    def test_nontrading_balance_operations_are_not_realized_trading_pnl(self):
        self.save([self.deal(1),self.deal(2,"1000",Action=2)])
        self.assertEqual(self.store.report(self.filters)["totals"][0]["deals"],1)
        self.assertEqual(self.store.activity(self.filters)["total"],2)

    def test_volume_ext_and_entry_costs(self):
        row = self.deal(Entry=0,Profit="0",VolumeExt=150000000)
        self.assertEqual(Decimal(row["lots"]),Decimal("1.5"))
        self.assertEqual(Decimal(row["net"]),Decimal("-0.04"))

    def test_failed_transaction_does_not_advance_checkpoint(self):
        with self.assertRaises(KeyError):
            self.save([self.deal(),{}])
        self.assertIsNone(self.store.checkpoint("real"))
        self.assertEqual(self.store.activity(self.filters)["total"],0)

    def test_cross_worker_lease_and_job_recovery(self):
        other = HistoryStore(self.store.path)
        self.assertTrue(self.store.lease("history-sync","one"))
        self.assertFalse(other.lease("history-sync","two"))
        claimed = self.store.claim("one")
        self.assertEqual(claimed["id"],self.job)
        self.assertIsNone(other.claim("two"))
        with self.store.connect() as con:
            con.execute("UPDATE history_jobs SET lease_until=0")
        self.assertEqual(other.claim("two")["id"],self.job)
        self.assertFalse(other.lease("history-sync","two"))

    def test_pagination_and_exports_match_scoped_totals(self):
        self.save([self.deal(1),self.deal(2,"0.2")])
        report = self.store.activity(self.filters,page=2,page_size=1)
        self.assertEqual(report["total"],2)
        self.assertEqual(len(report["rows"]),1)
        content = self.store.export(self.filters,"csv").decode("utf-8-sig")
        self.assertIn("TOTAL,USD",content)
        self.assertIn("0.22",content)
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(self.store.export(self.filters,"xlsx")),read_only=True)
        rows = list(book.active.values)
        self.assertEqual(rows[-1][0:2],("TOTAL","USD"))

    def test_snapshot_uses_actual_source_time_and_preserves_unknown_valuation(self):
        now = time.time()
        view = {"last_update":now,"users":[{"Login":10,"Group":"real","Currency":"USD","Balance":"100","Equity":"90",
                 "Margin":"20","Free Margin":"70","Floating PnL":"-10"}],
                "positions":[{"Ticket":11,"Login":10,"Symbol":"EURUSD","Action":0,"Volume":1,"Profit":-10,"ContractSize":100000,"PriceCurrent":1.1,"RateProfit":1}]}
        self.assertTrue(self.store.snapshots(view))
        self.store.snapshots(view)
        today = datetime.now(timezone.utc).date().isoformat()
        filters = {"from_date":today,"to_date":today}
        self.assertEqual(self.store.snapshot_report(filters)["total"],1)
        row = self.store.snapshot_report(filters,True)["rows"][0]
        self.assertIsNone(row["gross_notional"])
        self.assertEqual(row["source_ts"],now)
        self.assertEqual(row["net_lots"],"1")
        self.assertFalse(self.store.snapshots({**view,"last_update":now-1000}))

    def test_worker_failure_retries_and_preserves_checkpoint(self):
        class Provider:
            calls = 0
            def groups(self): return ["real"]
            def fetch(self,*args):
                self.calls += 1
                raise RuntimeError("offline")
        provider = Provider()
        worker = HistoryWorker(self.store,provider,threading.Event())
        self.store.lease("history-sync",worker.owner)
        job = self.store.claim(worker.owner)
        with patch.object(worker.stop,"wait",return_value=False):
            worker.execute(job,self.store.settings())
        self.assertEqual(provider.calls,3)
        self.assertIsNone(self.store.checkpoint("real"))
        self.assertEqual(self.store.status()["jobs"][0]["status"],"failed")

    def test_source_change_cannot_mix_two_manager_archives(self):
        self.store.bind_source("source-a")
        self.store.bind_source("source-a")
        with self.assertRaises(RuntimeError):
            self.store.bind_source("source-b")

    def test_old_day_aggregate_is_removed_when_source_timestamp_is_corrected(self):
        self.save([self.deal()])
        self.save([self.deal(Time=self.start+86410)])
        rows = self.store.report(self.filters)["rows"]
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["label"],"2026-10-02")

    def test_stale_worker_cannot_commit_or_finish_another_workers_job(self):
        self.store.lease("history-sync","one")
        self.store.claim("one")
        with self.assertRaises(RuntimeError):
            self.store.save_batch(self.job,"real",self.start,self.start+100,[self.deal()],owner="two")
        self.store.finish(self.job,owner="two")
        self.assertEqual(self.store.status()["jobs"][0]["status"],"running")

    def test_statistics_numeric_sort_and_closed_execution_filter(self):
        self.save([self.deal(1,"10",Entry=0),self.deal(2,"2",Symbol="GBPUSD"),self.deal(3,"-1",Symbol="XAUUSD")])
        result = self.store.report(self.filters,"symbol",sort="net",descending=True)
        self.assertEqual([row["label"] for row in result["rows"]],["EURUSD","GBPUSD","XAUUSD"])
        self.assertEqual(self.store.activity(self.filters,"closed")["total"],2)
        self.assertEqual(result["statistics"][0]["winning_accounts"],1)

    def test_export_neutralizes_source_formula_strings(self):
        self.save([self.deal(Symbol="-cmd|'payload'!A0")])
        exported = self.store.export({**self.filters,"report":"trading-activity"},"csv").decode("utf-8-sig")
        self.assertIn("'-cmd|",exported)

    def test_successful_worker_commits_and_resumes_without_duplicates(self):
        store, row = self.store, self.deal()
        class Provider:
            def groups(self): return ["real"]
            def fetch(self,*args): return [row]
        worker = HistoryWorker(store,Provider(),threading.Event())
        store.lease("history-sync",worker.owner)
        job = store.claim(worker.owner)
        with patch("services.history_worker.time.time",return_value=self.start+20):
            worker.execute(job,store.settings())
        self.assertEqual(store.status()["jobs"][0]["status"],"completed")
        self.assertEqual(store.checkpoint("real")["through_ts"],self.start+20)
        self.assertEqual(store.activity(self.filters)["total"],1)

    def test_provider_uses_group_order_method_and_retains_departed_accounts(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from services.history_api import MT5HistoryProvider
        sdk_calls = []
        timestamp = self.start
        class SDK:
            def HistoryRequestByGroup(self,group,start,end):
                sdk_calls.append(group)
                return [{"Order":9,"Login":10,"TimeDone":timestamp+10,"Type":0,"Symbol":"EURUSD","VolumeInitial":10000}]
            def HistoryRequest(self,*args):
                raise AssertionError("Login-only method must not receive a group")
        @contextmanager
        def session(): yield SDK()
        app = SimpleNamespace(configured_manager_credentials=lambda:("server",1,"not-printed"),
                              yielding_history_manager_session=session,request_users_by_group=lambda *args:([],None),
                              request_deals_by_group=lambda *args:([{"Deal":1,"Login":10,"Time":timestamp+10,"Action":0,"Entry":0,"Symbol":"EURUSD","Volume":10000,"Profit":0,"Commission":0,"Storage":0,"Fee":0}],None),
                              _value=value,_unwrap_response=lambda response:(None,response,""),
                              _response_to_data=lambda response,*args:(response,None),_as_list=list)
        provider = MT5HistoryProvider(app,self.store)
        manager = SimpleNamespace(cache_lock=threading.RLock(),cache={"group_currencies":{"real":"USD"}})
        with patch("mt5_connection.get_manager",return_value=manager):
            records = provider.fetch("real",timestamp,timestamp+100)
        self.assertEqual(sdk_calls,["real"])
        self.assertEqual({row["kind"] for row in records},{"deal","order"})
        self.assertEqual({row["currency"] for row in records},{"USD"})


class HistoricalAPITests(unittest.TestCase):
    def setUp(self):
        import api
        from fastapi.testclient import TestClient
        self.api = api
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name)/"api.db")
        self.path_patch = patch.object(api,"DB_PATH",self.path)
        self.path_patch.start()
        api.init_db()
        self.store = api._HISTORY_WORKER.store
        self.store_patch = patch.object(self.store,"path",self.path)
        self.store_patch.start()
        self.store.migrate("2026-10-01")
        self.user = {"id":1,"username":"test-admin","is_admin":True,"must_change_pw":False}
        self.auth = patch.object(api,"_verify_session_token",side_effect=lambda _:self.user)
        self.auth.start()
        self.client = TestClient(api.api,raise_server_exceptions=False)
        self.headers = {"Authorization":"Bearer test"}
        self.query = "?from_date=2026-10-01&to_date=2026-10-25"

    def tearDown(self):
        self.client.close()
        self.auth.stop()
        self.store_patch.stop()
        self.path_patch.stop()
        self.temp.cleanup()

    def test_authorization_and_date_validation(self):
        path = "/api/dealing-risk/analytics/pnl"
        self.assertEqual(self.client.get(path+self.query).status_code,401)
        self.user["is_admin"] = False
        self.assertEqual(self.client.get(path+self.query,headers=self.headers).status_code,403)
        self.assertEqual(self.client.post("/api/dealing-risk/sync/run",json={},headers=self.headers).status_code,403)
        self.user["is_admin"] = True
        response = self.client.get(path+"?from_date=2026-10-25&to_date=2026-10-01",headers=self.headers)
        self.assertEqual(response.status_code,422)

    def test_all_reports_read_database_while_mt5_offline(self):
        with patch.object(self.api,"get_configured_manager",side_effect=AssertionError("Reports must not call MT5")):
            for report in ("overview","pnl","accounts","exposure","trading-activity","risk-events"):
                response = self.client.get("/api/dealing-risk/analytics/"+report+self.query,headers=self.headers)
                self.assertEqual(response.status_code,200,(report,response.text))
                self.assertEqual(response.json()["rows"],[])

    def test_export_is_background_and_owned_by_requesting_admin(self):
        payload = {"from_date":"2026-10-01","to_date":"2026-10-25","format":"xlsx"}
        response = self.client.post("/api/dealing-risk/analytics/exports",json=payload,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        job_id = response.json()["id"]
        path = "/api/dealing-risk/analytics/exports/"+job_id
        self.assertEqual(self.client.get(path,headers=self.headers).json()["status"],"queued")
        self.assertEqual(self.client.get(path+"/download",headers=self.headers).status_code,409)
        self.user["username"] = "another-admin"
        self.assertEqual(self.client.get(path,headers=self.headers).status_code,404)

    def test_schedule_changes_do_not_change_live_refresh_config(self):
        before = self.client.get("/api/dealing-risk/config",headers=self.headers).json()
        response = self.client.put("/api/dealing-risk/sync/settings",json={"interval_minutes":30,"snapshot_seconds":300,
                          "reconciliation_hour":2,"backfill_from":"2026-10-01"},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.store.settings()["interval_minutes"],30)
        self.assertEqual(self.client.get("/api/dealing-risk/config",headers=self.headers).json(),before)


if __name__ == "__main__":
    unittest.main()
