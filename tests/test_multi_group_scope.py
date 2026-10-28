from __future__ import annotations

import unittest
from unittest.mock import patch

import api


class MultiGroupScopeTests(unittest.TestCase):
    def setUp(self):
        self.groups = [f"Broker\\Group{index}" for index in range(1, 5)]
        self.users = {
            group: [{"Login": 1000 + index, "Group": group, "Name": f"User {index}", "Currency": "USD"}]
            for index, group in enumerate(self.groups, start=1)
        }

    def test_live_selection_accepts_more_than_one_thousand_groups(self):
        groups = [f"Broker\\Group{index}" for index in range(1200)]
        request = api.LiveRiskRequest(selected_groups=groups)
        self.assertEqual(request.selected_groups, groups)

    def test_four_groups_use_exact_user_requests(self):
        requested = []

        def request_users(_api, group):
            requested.append(group)
            if group == "*":
                return list(self.users[self.groups[0]]), None
            return self.users[group], None

        with patch.object(api, "request_users_by_group", side_effect=request_users):
            users, group_map, warnings = api._collect_user_scope(object(), self.groups)

        self.assertEqual(requested, self.groups)
        self.assertEqual(set(users), {1001, 1002, 1003, 1004})
        self.assertEqual(set(group_map.values()), set(self.groups))
        self.assertEqual(warnings, [])

    def test_four_groups_use_exact_deal_and_daily_requests(self):
        deal_requests = []
        daily_requests = []

        def request_deals(_api, group, _from_ts, _to_ts):
            deal_requests.append(group)
            return [{"Login": 1000 + self.groups.index(group) + 1}], None

        def request_daily(_api, group, _from_ts, _to_ts):
            daily_requests.append(group)
            requested_groups = group.split(",")
            return [
                {"Login": 1000 + self.groups.index(requested_group) + 1}
                for requested_group in requested_groups
            ], None

        allowed_logins = {1001, 1002, 1003, 1004}
        with (
            patch.object(api, "request_deals_by_group", side_effect=request_deals),
            patch.object(api, "request_daily_by_group", side_effect=request_daily),
        ):
            deals, deal_warnings = api._fetch_scoped_deals(object(), self.groups, 1, 2, allowed_logins)
            daily, daily_warnings = api._fetch_scoped_daily_records(object(), self.groups, 1, 2, allowed_logins)

        self.assertEqual(deal_requests, self.groups)
        self.assertEqual(daily_requests, [",".join(self.groups)])
        self.assertEqual({row["Login"] for row in deals}, allowed_logins)
        self.assertEqual({row["Login"] for row in daily}, allowed_logins)
        self.assertEqual(deal_warnings, [])
        self.assertEqual(daily_warnings, [])

    def test_empty_combined_daily_response_falls_back_to_each_group(self):
        requested = []

        def request_daily(_api, group, _from_ts, _to_ts):
            requested.append(group)
            if "," in group:
                return [], None
            return [{"Login": 1000 + self.groups.index(group) + 1}], None

        allowed_logins = {1001, 1002, 1003, 1004}
        with patch.object(api, "request_daily_by_group", side_effect=request_daily):
            rows, warnings = api._fetch_scoped_daily_records(object(), self.groups, 1, 2, allowed_logins)

        self.assertEqual(requested, [",".join(self.groups), *self.groups])
        self.assertEqual({row["Login"] for row in rows}, allowed_logins)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
