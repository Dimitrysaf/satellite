"""Tests for shared constants and dynamic plugin loading."""

import tempfile
import unittest

from satellite import APP_ID, DBUS_NAME, DBUS_PATH, SCHEMA_ID
from satellite.plugins import PluginManager


class SharedConstantsTests(unittest.TestCase):
    def test_application_identifiers(self):
        self.assertEqual(APP_ID, "org.x.Satellite")
        self.assertEqual(DBUS_NAME, "org.x.Satellite")
        self.assertEqual(DBUS_PATH, "/org/x/Satellite")
        self.assertEqual(SCHEMA_ID, "org.x.satellite")


class PluginLoadingTests(unittest.TestCase):
    def test_all_plugin_modules_are_discovered(self):
        expected = {
            "battery", "clipboard", "connectivity_report", "contacts", "lock",
            "mousepad", "mpris", "notifications", "pausemusic", "ping", "presenter",
            "ring", "runcommand", "sftp", "share", "sms", "systemvolume", "telephony",
        }
        with tempfile.TemporaryDirectory() as config_dir:
            manager = PluginManager(config_dir)
        self.assertEqual(set(manager._modules), expected)

    def test_enabled_by_default_plugins_load(self):
        with tempfile.TemporaryDirectory() as config_dir:
            manager = PluginManager(config_dir)
        self.assertIn("battery", manager.plugins)


if __name__ == "__main__":
    unittest.main()
