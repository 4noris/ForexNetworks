from __future__ import annotations

import unittest
from datetime import date

import api


class CidAuditTests(unittest.TestCase):
    def test_parse_login_message_extracts_device_and_geo_evidence(self):
        parsed = api.parse_cid_login_message(
            "900909488015: login (Client Android build 5871, "
            "cid:C4AE4CF862B0599EBC72E54FEDD1A602, point: Access Server 2, "
            "ping:177.85 ms, geo:NP, Kathmandu, Example ISP)"
        )

        self.assertEqual(parsed["login"], 900909488015)
        self.assertEqual(parsed["computer_id"], "c4ae4cf862b0599ebc72e54fedd1a602")
        self.assertEqual(parsed["device_type"], "Android")
        self.assertEqual(parsed["build"], 5871)
        self.assertEqual(parsed["country"], "NP")
        self.assertEqual(parsed["city"], "Kathmandu")
        self.assertEqual(parsed["isp"], "Example ISP")

    def test_build_events_rejects_non_login_journal_messages(self):
        records = [
            {"Time": 1_800_000_000, "IP": "8.8.8.8", "Message": "ordinary server log"},
            {
                "Time": 1_800_000_001,
                "IP": "1.1.1.1",
                "Message": (
                    "1001: login as investor (Client iPhone build 5830, "
                    "cid:abcdef0123456789, point: Access 1, ping:20 ms, "
                    "geo:IN, Mumbai, Carrier)"
                ),
            },
        ]

        events = api.build_cid_login_events(records)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["login"], 1001)
        self.assertEqual(events[0]["ip_address"], "1.1.1.1")
        self.assertTrue(events[0]["occurred_at"].endswith("+00:00"))

    def test_analysis_flags_shared_cid_ip_multiple_devices_and_countries(self):
        events = [
            {"event_key": "1", "login": 1001, "computer_id": "cid-a", "ip_address": "8.8.8.8", "country": "IN"},
            {"event_key": "2", "login": 1002, "computer_id": "cid-a", "ip_address": "8.8.8.8", "country": "NP"},
            {"event_key": "3", "login": 1001, "computer_id": "cid-b", "ip_address": "8.8.8.8", "country": "IN"},
            {"event_key": "4", "login": 1003, "computer_id": "cid-c", "ip_address": "8.8.8.8", "country": "IN"},
        ]

        result = api.analyze_cid_login_events(events)
        by_key = {row["event_key"]: row for row in result["events"]}

        self.assertTrue(by_key["1"]["flags"]["shared_cid"])
        self.assertTrue(by_key["1"]["flags"]["multiple_cids"])
        self.assertTrue(by_key["1"]["flags"]["multiple_countries"])
        self.assertTrue(by_key["4"]["flags"]["shared_ip"])
        self.assertEqual(result["summary"]["shared_computer_ids"], 1)
        self.assertEqual(result["summary"]["shared_ip_addresses"], 1)
        self.assertEqual(result["risk_summary"]["shared_cid"], 2)
        self.assertEqual(result["risk_summary"]["multiple_cids"], 2)
        self.assertEqual(result["risk_summary"]["multiple_countries"], 2)
        self.assertEqual(result["cid_groups"][0]["countries"], ["IN", "NP"])

    def test_custom_and_all_history_ranges_are_accepted(self):
        custom = api.CidAuditRequest(
            server="example:443",
            login=1001,
            password="secret",
            from_date=date(2025, 1, 1),
            to_date=date(2026, 1, 1),
        )
        all_history = api.CidAuditRequest(
            server="example:443",
            login=1001,
            password="secret",
            all_history=True,
        )

        self.assertEqual(custom.from_date, date(2025, 1, 1))
        self.assertTrue(all_history.all_history)

    def test_incomplete_or_reversed_custom_range_is_rejected(self):
        with self.assertRaises(ValueError):
            api.CidAuditRequest(
                server="example:443",
                login=1001,
                password="secret",
                from_date=date(2026, 1, 1),
            )
        with self.assertRaises(ValueError):
            api.CidAuditRequest(
                server="example:443",
                login=1001,
                password="secret",
                from_date=date(2026, 1, 2),
                to_date=date(2026, 1, 1),
            )


if __name__ == "__main__":
    unittest.main()


def test_cid_audit_uses_configured_manager_when_profile_has_no_password(monkeypatch):
    from contextlib import contextmanager

    sentinel = object()

    @contextmanager
    def manager_session():
        yield sentinel

    monkeypatch.setattr(api, "yielding_history_manager_session", manager_session)
    monkeypatch.setattr(
        api, "request_cid_login_journal",
        lambda manager, start, end: ([{"login": 1001}], None)
        if manager is sentinel and start == 1 and end == 2 else ([], "wrong manager"),
    )

    events, error = api.request_cid_login_journal_isolated("", 0, "", 1, 2)

    assert events == [{"login": 1001}]
    assert error is None
