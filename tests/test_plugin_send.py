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

    def test_pointer_moves_dont_redraw_the_window(self):
        # Each "devices changed" rebuilds the device page (~30 ms); at 60 moves a second the
        # presenter pointer and the touchpad fell further and further behind.
        from satellite.plugins.mousepad import Plugin as Mousepad
        from satellite.plugins.presenter import Plugin as Presenter
        from tests.fakes import FakeDaemon, LINK, packet
        for plugin, packet_type in ((Presenter(display=lambda *_a: None, limitation=lambda: None),
                                     "kdeconnect.presenter"),
                                    (Mousepad(), "kdeconnect.mousepad.request")):
            with self.subTest(plugin=plugin.plugin_id):
                daemon = FakeDaemon(devices={LINK["id"]: {"id": LINK["id"]}})
                daemon._notify_devices_changed = mock.Mock()
                with mock.patch("satellite.plugins.mousepad.desktop_input"):
                    plugin.handle_packet(daemon, LINK, packet(packet_type, {"dx": 0.01, "dy": 0}))
                daemon._notify_devices_changed.assert_not_called()
                self.assertNotIn("plugin_data", daemon._devices[LINK["id"]])

    def test_browse_files_asks_the_phone_to_start_browsing(self):
        from satellite.plugins.sftp import Plugin
        daemon = mock.Mock()
        daemon._send_plugin_packet.return_value = True
        self.assertTrue(Plugin.send(daemon, "d", "kdeconnect.sftp.request", {"startBrowsing": True}))
        daemon._send_plugin_packet.assert_called_once_with("d", "kdeconnect.sftp.request", {"startBrowsing": True})
        self.assertFalse(Plugin.send(daemon, "d", "kdeconnect.ping", {}))


if __name__ == "__main__":
    unittest.main()
