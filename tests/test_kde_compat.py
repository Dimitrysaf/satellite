"""Behaviour KDE Connect for Android relies on: identity, pairing code, timeouts and reconnects."""

import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from OpenSSL import crypto

PHONE = "a1b2c3d4e5f6a7b8" * 2


class KdeCompatTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        env = mock.patch.dict(os.environ, {"HOME": self.home.name})
        env.start()
        self.addCleanup(env.stop)
        from daemon import daemon as module
        self.module = module
        self.daemon = module.SatelliteDaemon()
        self.addCleanup(self.daemon._stop.set)
        self.sent = []
        self.daemon._send_packet = lambda _link, data: self.sent.append(json.loads(data))

    def link(self, paired=True, certificate=None):
        link = {"id": PHONE, "name": "Galaxy", "paired": paired, "send_lock": threading.Lock(),
                "socket": mock.Mock(), "certificate": certificate}
        self.daemon._active[PHONE] = link
        self.daemon._devices[PHONE] = {"id": PHONE, "name": "Galaxy", "secure": True,
                                       "paired": paired, "connected": paired}
        return link

    def pair_packets(self):
        return [p["body"] for p in self.sent if p["type"] == "kdeconnect.pair"]

    def test_outgoing_identity_is_complete(self):
        # Android closes the connection when the name, type or capabilities are missing.
        body = json.loads(self.daemon._connection_packet(PHONE))["body"]
        for key in ("deviceId", "deviceName", "deviceType", "protocolVersion",
                    "incomingCapabilities", "outgoingCapabilities"):
            self.assertIn(key, body)
        self.assertEqual((body["targetDeviceId"], body["targetProtocolVersion"]), (PHONE, 8))

    def test_verification_code_matches_kde_connect(self):
        key = crypto.PKey()
        key.generate_key(crypto.TYPE_RSA, 2048)
        peer = crypto.X509()
        peer.set_pubkey(key)
        mine = crypto.dump_publickey(crypto.FILETYPE_ASN1, self.daemon._certificate.get_pubkey())
        theirs = crypto.dump_publickey(crypto.FILETYPE_ASN1, key)
        a, b = (mine, theirs) if mine >= theirs else (theirs, mine)  # KDE Connect: larger key first
        expected = hashlib.sha256(a + b + b"1761768000").hexdigest()[:8].upper()
        self.assertEqual(self.daemon._pairing_verification_code(peer, 1761768000), expected)

    def test_pair_request_times_out_and_tells_the_phone(self):
        self.link(paired=False, certificate=self.daemon._certificate)
        with mock.patch.object(self.module, "PAIR_TIMEOUT", 0.05):
            self.assertTrue(self.daemon.request_pair(PHONE))
            time.sleep(0.3)
        self.assertFalse(self.daemon._devices[PHONE]["pair_pending"])
        self.assertEqual(self.pair_packets()[-1], {"pair": False})

    def test_cancel_pairing_tells_the_phone(self):
        self.link(paired=False, certificate=self.daemon._certificate)
        self.daemon.request_pair(PHONE)
        self.assertTrue(self.daemon.cancel_pair(PHONE))
        self.assertFalse(self.daemon._devices[PHONE]["pair_pending"])
        self.assertEqual(self.pair_packets()[-1], {"pair": False})

    def test_connecting_waits_after_a_broadcast(self):
        device = {"id": PHONE, "address": "192.168.1.12", "port": 1716}
        with mock.patch.object(self.module, "CONTACT_GAP", 0.2), \
                mock.patch.object(self.daemon, "_connect_outgoing") as connect:
            self.daemon._last_broadcast = time.monotonic()
            self.daemon._connect_to_device(device)
            self.daemon._connect_to_device(device)  # a second trigger doesn't queue twice
            time.sleep(0.05)
            connect.assert_not_called()
            time.sleep(0.4)
        self.assertEqual(connect.call_count, 1)

    def test_replaced_link_is_not_shown_as_disconnected(self):
        old = self.link()
        with mock.patch.object(self.module, "LINK_GRACE", 0.1), \
                mock.patch.object(self.daemon.plugins, "device_disconnected") as disconnected:
            self.daemon._remove_link(old)
            self.daemon._active[PHONE] = dict(old)  # Android's new connection arrives
            time.sleep(0.3)
        disconnected.assert_not_called()
        self.assertTrue(self.daemon._devices[PHONE]["connected"])

    def test_closed_link_without_replacement_disconnects(self):
        old = self.link()
        with mock.patch.object(self.module, "LINK_GRACE", 0.1), \
                mock.patch.object(self.daemon.plugins, "device_disconnected") as disconnected, \
                mock.patch.object(self.daemon, "_schedule_reconnect"):
            self.daemon._remove_link(old)
            self.assertTrue(self.daemon._devices[PHONE]["connected"])
            time.sleep(0.3)
        disconnected.assert_called_once()
        self.assertFalse(self.daemon._devices[PHONE]["connected"])


if __name__ == "__main__":
    unittest.main()
