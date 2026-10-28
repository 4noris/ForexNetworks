import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import api
import mt5_connection


class SingleManagerTests(unittest.TestCase):
    def test_only_standard_env_credentials_are_loaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                'MT5_MANAGER_SERVER=example:443\n'
                'MT5_MANAGER_LOGIN=123\n'
                'MT5_MANAGER_PASSWORD=secret\n'
                'MT5_MANAGERS_JSON=[{"id":"other","server":"elsewhere"}]\n',
                encoding="utf-8",
            )
            with patch.object(api, "_MANAGER_CONFIG_PATH", env_path):
                configs = api.configured_manager_configs()
                self.assertEqual(list(configs), ["default"])
                self.assertEqual(api.configured_manager_credentials(), ("example:443", 123, "secret"))
                with self.assertRaises(RuntimeError):
                    api.configured_manager_credentials("other")

    def test_registry_rejects_second_manager_before_connecting(self):
        with self.assertRaises(ValueError):
            mt5_connection.start_manager("example:443", 123, "secret", "other")


if __name__ == "__main__":
    unittest.main()
