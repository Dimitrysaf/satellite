"""Finding devices: manual addresses, broadcast switch, mDNS records."""

import os
import tempfile
import unittest
from unittest import mock

from satellite.discovery import DiscoverySettings, clean_addresses, txt_records


class SettingsTests(unittest.TestCase):
    def test_defaults_and_saving(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = DiscoverySettings(folder)
            self.assertTrue(settings.get("broadcast"))
            self.assertTrue(settings.get("mdns"))
            settings.set("custom_devices", [" 10.0.0.5 ", "10.0.0.5", "phone.lan", "bad host!", 7])
            self.assertEqual(DiscoverySettings(folder).get("custom_devices"), ["10.0.0.5", "phone.lan"])

    def test_clean_addresses_accepts_ipv6(self):
        self.assertEqual(clean_addresses(["fd00::12", "a" * 300]), ["fd00::12"])

    def test_txt_records(self):
        records = [list(b"id=abc"), list(b"name=Pixel=7"), list(b"junk")]
        self.assertEqual(txt_records(records), {"id": "abc", "name": "Pixel=7"})


class DaemonDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"HOME": self.home.name})
        self.env.start()
        from daemon.daemon import SatelliteDaemon
        self.daemon = SatelliteDaemon()
        self.daemon._udp = mock.Mock()
        self.daemon.tcp_port = 1716

    def tearDown(self):
        self.env.stop()
        self.home.cleanup()

    def sent_to(self):
        return [call.args[1] for call in self.daemon._udp.sendto.call_args_list]

    def test_added_addresses_are_contacted_even_without_broadcast(self):
        self.daemon.discovery.set("custom_devices", ["10.8.0.2"])
        self.daemon.discovery.set("broadcast", False)
        with mock.patch("socket.if_nameindex", return_value=[]):
            self.daemon._broadcast()
        self.assertEqual(self.sent_to(), [("10.8.0.2", 1716)])

    def test_broadcast_can_be_switched_off(self):
        self.daemon.discovery.set("broadcast", False)
        with mock.patch("socket.if_nameindex", return_value=[]):
            self.daemon._broadcast()
        self.assertEqual(self.sent_to(), [])

    def test_mdns_discovery_connects_like_a_broadcast(self):
        with mock.patch.object(self.daemon, "_connect_to_device") as connect:
            self.daemon.device_discovered("a1b2c3d4e5f6a7b8" * 2, "Pixel 7", "phone", 8, "192.168.1.42", 1716)
            self.daemon.device_discovered(self.daemon.device_id, "Me", "desktop", 8, "127.0.0.1", 1716)
            self.daemon.device_discovered("c3c3c3c3c3c3c3c3" * 2, "Old", "phone", 7, "192.168.1.9", 1716)
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(self.daemon._devices["a1b2c3d4e5f6a7b8" * 2]["address"], "192.168.1.42")


if __name__ == "__main__":
    unittest.main()
