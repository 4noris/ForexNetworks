from __future__ import annotations

import unittest

import api


class FakeManagerCurrent:
    def __init__(self, groups):
        self._groups = groups

    def GroupGet(self):
        return self._groups


class FakeManagerApi:
    def __init__(self, manager_groups, group_responses):
        self._manager_current = FakeManagerCurrent(manager_groups)
        self._group_responses = group_responses
        self.requested_masks = []

    def ManagerCurrent(self):
        return self._manager_current

    def GroupRequestArray(self, mask):
        self.requested_masks.append(mask)
        return self._group_responses.get(mask, [])


class ManagerGroupAccessTests(unittest.TestCase):
    def test_request_groups_filters_to_manager_access(self):
        access_groups = ["ByteFX\\*", "ByteFX_A\\*"]
        group_responses = {
            "ByteFX\\*": ["ByteFX\\Alpha", "ByteFX\\Beta", "DELETE"],
            "ByteFX_A\\*": ["ByteFX_A\\Beta", "demo", "managers", "preliminary"],
            "*": ["DELETE", "demo", "managers", "preliminary"],
        }
        fake_api = FakeManagerApi(access_groups, group_responses)

        groups, err = api.request_groups(fake_api)

        self.assertIsNone(err)
        self.assertEqual(groups, ["ByteFX\\Alpha", "ByteFX\\Beta", "ByteFX_A\\Beta"])
        self.assertNotIn("DELETE", groups)
        self.assertNotIn("demo", groups)
        self.assertNotIn("managers", groups)
        self.assertNotIn("preliminary", groups)
        self.assertEqual(fake_api.requested_masks, ["ByteFX\\*", "ByteFX_A\\*"])
        self.assertNotIn("*", fake_api.requested_masks)


if __name__ == "__main__":
    unittest.main()
