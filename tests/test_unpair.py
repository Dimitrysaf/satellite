"""Unpairing, with the phone connected or not."""

import json
import os
import tempfile
import threading
import unittest
from unittest import mock

PHONE = "a1b2c3d4e5f6a7b8" * 2


class UnpairTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        env = mock.patch.dict(os.environ, {"HOME": self.home.name})
        env.start()
        self.addCleanup(env.stop)
        from daemon.daemon import SatelliteDaemon
        self.daemon = SatelliteDaemon()
        self.daemon._save_trusted_certificate(PHONE, self.daemon._certificate)
        self.daemon._devices[PHONE] = {"id": PHONE, "name": "Galaxy", "paired": True, "connected": False}
        self.sent = []
        self.daemon._send_packet = lambda _link, data: self.sent.append(json.loads(data))

    def connect(self):
        link = {"id": PHONE, "name": "Galaxy", "paired": True, "send_lock": threading.Lock()}
        self.daemon._active[PHONE] = link
        self.daemon._devices[PHONE]["connected"] = True
        return link

    def trusted(self):
        return os.path.exists(self.daemon._trusted_certificate_path(PHONE))

    def test_unpair_while_offline(self):
        self.assertTrue(self.daemon.unpair(PHONE))
        self.assertFalse(self.trusted())
        self.assertFalse(self.daemon._devices[PHONE]["paired"])
        self.assertEqual(self.sent, [])

    def test_unpair_while_connected_tells_the_phone(self):
        link = self.connect()
        self.assertTrue(self.daemon.unpair(PHONE))
        self.assertFalse(self.trusted())
        self.assertFalse(link["paired"])
        self.assertEqual([(p["type"], p["body"]) for p in self.sent], [("kdeconnect.pair", {"pair": False})])

    def test_unpair_still_forgets_the_phone_if_telling_it_fails(self):
        self.connect()

        def broken(_link, _data):
            raise OSError("connection reset")
        self.daemon._send_packet = broken
        self.assertTrue(self.daemon.unpair(PHONE))
        self.assertFalse(self.trusted())
        self.assertFalse(self.daemon._devices[PHONE]["paired"])

    def test_phone_still_acting_paired_is_told_it_is_not(self):
        self.daemon.unpair(PHONE)
        link = self.connect()
        link["paired"] = False
        with mock.patch.object(self.daemon.plugins, "handle_packet") as handle:
            self.daemon._handle_packet(link, {"type": "kdeconnect.ping", "body": {}})
        handle.assert_not_called()
        self.assertEqual([(p["type"], p["body"]) for p in self.sent], [("kdeconnect.pair", {"pair": False})])

    def test_cannot_unpair_a_device_that_is_not_paired(self):
        self.daemon._devices[PHONE]["paired"] = False
        self.assertFalse(self.daemon.unpair(PHONE))
        self.assertTrue(self.trusted())


if __name__ == "__main__":
    unittest.main()
