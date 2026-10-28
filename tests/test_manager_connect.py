import unittest
from unittest.mock import patch

import api


class FakeManagerApi:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def Connect(self, *args):
        self.calls.append(args)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class ManagerConnectTests(unittest.TestCase):
    def test_does_not_retry_after_accepted_signature_returns_false(self):
        handle = FakeManagerApi([False])
        with patch.object(api.MT5Manager, "ManagerAPI", return_value=handle):
            connected, error = api.connect_manager("server:443", 1, "secret", timeout_ms=5000)

        self.assertIsNone(connected)
        self.assertTrue(error)
        self.assertEqual(len(handle.calls), 1)
        self.assertEqual(handle.calls[0][-1], 5000)

    def test_tries_legacy_signature_only_when_signature_is_unsupported(self):
        handle = FakeManagerApi([TypeError(), True])
        with patch.object(api.MT5Manager, "ManagerAPI", return_value=handle):
            connected, error = api.connect_manager("server:443", 1, "secret", timeout_ms=5000)

        self.assertIs(connected, handle)
        self.assertIsNone(error)
        self.assertEqual(len(handle.calls), 2)


if __name__ == "__main__":
    unittest.main()
