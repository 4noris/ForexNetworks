from datetime import datetime, timedelta, timezone

from services import brokerage_pnl as pnl


class FakeManager:
    def __init__(self, missing_opening=False, failed_deals=False):
        self.missing_opening = missing_opening
        self.failed_deals = failed_deals

    def GroupRequest(self, group):
        return {"Currency": "USD"}

    def UserGetByGroup(self, group):
        return [{"Login": 10, "Group": group}, {"Login": 20, "Group": group}]

    def DailyRequestByGroup(self, group, start, end):
        opening = datetime.fromtimestamp(start, timezone.utc).day == 1
        if opening and self.missing_opening:
            return []
        return [{"Login": login, "Group": group, "Datetime": end,
                 "Equity": 1000 if opening else 1100, "Currency": "USD"}
                for login in (10, 20)]

    def DealRequestByGroup(self, group, start, end):
        if self.failed_deals:
            return 7, None, "History access denied"
        return [{"Login": login, "Deal": login + 100, "Time": start,
                 "Action": pnl.DEAL_BALANCE, "Profit": 50}
                for login in (10, 20)]


def make_report(manager, **kwargs):
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    return pnl.build_report(manager, ["real"], now.date() - timedelta(days=2),
                            now.date() - timedelta(days=1), now.date() - timedelta(days=1),
                            now.date() - timedelta(days=1), now=now, **kwargs)


def test_reconciliation_and_excluded_accounts():
    report = make_report(FakeManager(), excluded_logins=[20])
    accounts = report["accounts"]
    assert accounts["Login"].tolist() == [10]
    assert len(report["audit"]) == 1
    assert accounts.iloc[0]["Net PnL"] == 50
    assert pnl.aggregate_summary(accounts).iloc[0]["Broker PnL"] == -50


def test_missing_snapshot_never_produces_pnl():
    report = make_report(FakeManager(missing_opening=True), allow_live=True)
    assert report["accounts"]["Net PnL"].isna().all()
    assert pnl.aggregate_summary(report["accounts"]).iloc[0]["Net PnL"] is None


def test_missing_group_snapshot_recovers_from_single_login_history():
    class SingleLoginManager(FakeManager):
        def DailyRequestByGroup(self, group, start, end):
            return [row for row in super().DailyRequestByGroup(group, start, end)
                    if row["Login"] != 20 or datetime.fromtimestamp(start, timezone.utc).day != 1]

        def DailyRequest(self, login, start, end):
            assert login == 20
            return {"Login": login, "Group": "real", "Datetime": end,
                    "Equity": 1000, "Currency": "USD"}

    report = make_report(SingleLoginManager())
    recovered = report["accounts"].set_index("Login").loc[20]
    assert recovered["Opening Equity"] == 1000
    assert recovered["Net PnL"] == 50
    assert pnl.aggregate_summary(report["accounts"]).iloc[0]["Net PnL"] == 100


def test_daily_recovery_replaces_row_without_equity():
    class IncompleteDailyManager(FakeManager):
        def DailyRequestByGroup(self, group, start, end):
            rows = super().DailyRequestByGroup(group, start, end)
            if datetime.fromtimestamp(start, timezone.utc).day == 1:
                rows[1].pop("Equity")
            return rows

        def DailyRequest(self, login, start, end):
            assert login == 20
            return {"Login": login, "Group": "real", "Datetime": end,
                    "Equity": 1000, "Currency": "USD"}

    report = make_report(IncompleteDailyManager())
    assert report["accounts"].set_index("Login").loc[20, "Opening Equity"] == 1000
    assert pnl.aggregate_summary(report["accounts"]).iloc[0]["Net PnL"] == 100


def test_failed_deal_history_never_becomes_zero_flow():
    report = make_report(FakeManager(failed_deals=True))
    assert report["accounts"]["Deposit"].isna().all()
    assert report["accounts"]["Net PnL"].isna().all()


def test_incomplete_account_coverage_suppresses_full_scope_pnl():
    report = make_report(FakeManager())
    summary = pnl.aggregate_summary(report["accounts"], coverage=False)
    assert summary.iloc[0]["Net PnL"] is None
    assert summary.iloc[0]["Data Quality"] == "INCOMPLETE / PROVISIONAL"


def test_flow_categories_and_book_views():
    assert pnl.classify_balance_transaction({"Action": pnl.DEAL_CREDIT, "Profit": -25}) == ("Credit Out", 25)
    assert pnl.classify_balance_transaction({"Action": pnl.DEAL_BALANCE, "Profit": -5, "Comment": "swap charge"}) == (None, 0)
    assert pnl.pnl_views(-15, -10, -5, "A-Book")["Broker PnL"] == 15
    assert pnl.pnl_views(-15, -10, -5, "B-Book")["Broker PnL"] == 15
