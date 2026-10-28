"""Tests for shared constants and dynamic plugin loading."""

import os
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
            "battery", "clipboard", "connectivity_report", "contacts", "digitizer", "lock",
            "mousepad", "mpris", "mpriscontrol", "notifications", "pausemusic", "ping",
            "presenter", "remoteinput", "remotesystemvolume", "ring", "runcommand",
            "screensaver_inhibit", "sendnotifications", "shareinputdevices", "sftp", "share", "sms",
            "systemvolume", "telephony",
        }
        with tempfile.TemporaryDirectory() as config_dir:
            manager = PluginManager(config_dir)
        self.assertEqual(set(manager._modules), expected)

    def test_enabled_by_default_plugins_load(self):
        with tempfile.TemporaryDirectory() as config_dir:
            manager = PluginManager(config_dir)
        self.assertIn("battery", manager.plugins)


class PluginIconTests(unittest.TestCase):
    """Every plugin row needs an icon that icon themes really have."""

    def icons(self):
        with tempfile.TemporaryDirectory() as config_dir:
            manager = PluginManager(config_dir)
        return {plugin_id: plugin.icon for plugin_id, plugin in manager.plugins.items()}

    def test_icons_exist(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        # Mint-Y falls back to Adwaita, so Adwaita covers Mint's own theme too.
        for theme_name in ("Adwaita", "Papirus"):
            if not os.path.isdir(os.path.join("/usr/share/icons", theme_name)):
                continue
            theme = Gtk.IconTheme.new()
            theme.set_custom_theme(theme_name)
            for plugin_id, icon in self.icons().items():
                with self.subTest(theme=theme_name, plugin=plugin_id):
                    self.assertIsNotNone(theme.lookup_icon(icon, 16, 0), icon)

    def test_rows_look_different(self):
        icons = list(self.icons().values())
        repeated = {icon for icon in icons if icons.count(icon) > 1}
        self.assertEqual(repeated, set())


if __name__ == "__main__":
    unittest.main()
