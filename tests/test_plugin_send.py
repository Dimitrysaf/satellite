"""Every plugin that sends packets can send them."""

import importlib
import pkgutil
import unittest
from unittest import mock

import satellite.plugins as plugins_package


class PluginSendTests(unittest.TestCase):
    def test_every_plugin_with_outgoing_packets_has_send(self):
        for module in pkgutil.iter_modules(plugins_package.__path__):
            if module.name.startswith("_"):
                continue
            plugin = importlib.import_module("satellite.plugins." + module.name).Plugin
            if getattr(plugin, "outgoing_capabilities", ()):
                with self.subTest(plugin=module.name):
                    self.assertTrue(callable(getattr(plugin, "send", None)))

    def test_browse_files_asks_the_phone_to_start_browsing(self):
        from satellite.plugins.sftp import Plugin
        daemon = mock.Mock()
        daemon._send_plugin_packet.return_value = True
        self.assertTrue(Plugin.send(daemon, "d", "kdeconnect.sftp.request", {"startBrowsing": True}))
        daemon._send_plugin_packet.assert_called_once_with("d", "kdeconnect.sftp.request", {"startBrowsing": True})
        self.assertFalse(Plugin.send(daemon, "d", "kdeconnect.ping", {}))


if __name__ == "__main__":
    unittest.main()
