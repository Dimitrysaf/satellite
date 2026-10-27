"""File transfer between two real daemons over real TLS on loopback."""

import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from daemon.daemon import SatelliteDaemon
from satellite.plugins.share import Plugin as Share
from satellite.plugins.share import clean_filename, receive_file, send_file, unique_path
from satellite.tls import json_packet
from tests.fakes import FakeDaemon, packet


def make_daemon(home):
    with mock.patch.dict(os.environ, {"HOME": home}):
        return SatelliteDaemon()


class FileTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sender = make_daemon(os.path.join(self.tmp.name, "a"))
        self.receiver = make_daemon(os.path.join(self.tmp.name, "b"))
        for daemon, peer in ((self.sender, self.receiver), (self.receiver, self.sender)):
            daemon.verify_peer = lambda _id, cert, peer=peer: (
                cert.digest("sha256") == peer._certificate.digest("sha256"))
        self.wire = []
        self.sender._active[self.receiver.device_id] = {
            "id": self.receiver.device_id, "name": "Laptop B", "paired": True,
            "address": "127.0.0.1", "stream": mock.Mock(),
        }
        self.sender.send_packet = lambda _id, data: self.wire.append(data) or True
        self.sent_progress, self.got_progress = [], []
        self.sender.on_transfer_progress = self.sent_progress.append
        self.receiver.on_transfer_progress = self.got_progress.append
        self.link_to_sender = {"id": self.sender.device_id, "name": "Laptop A", "address": "127.0.0.1"}

    def make_file(self, size):
        path = os.path.join(self.tmp.name, "photo.bin")
        with open(path, "wb") as stream:
            stream.write(os.urandom(size))
        return path

    def test_file_arrives_intact(self):
        source = self.make_file(300_000)
        self.assertTrue(send_file(self.sender, self.receiver.device_id, source))
        offer = json.loads(self.wire[0])
        self.assertEqual(offer["type"], "kdeconnect.share.request")
        self.assertEqual(offer["payloadSize"], 300_000)
        self.assertEqual(offer["body"]["filename"], "photo.bin")

        destination = os.path.join(self.tmp.name, "downloads")
        result = receive_file(self.receiver, self.link_to_sender, offer, destination)
        self.assertEqual(os.path.basename(result), "photo.bin")
        with open(source, "rb") as a, open(result, "rb") as b:
            self.assertEqual(a.read(), b.read())
        self.assertEqual(os.listdir(destination), ["photo.bin"])  # no .part left behind
        self.assertTrue(self.got_progress[-1]["finished"] and not self.got_progress[-1]["error"])
        self.assertEqual(self.got_progress[-1]["direction"], "receive")
        for _ in range(50):
            if self.sent_progress and self.sent_progress[-1]["finished"]:
                break
            threading.Event().wait(0.1)
        self.assertEqual(self.sent_progress[-1]["direction"], "send")

    def test_untrusted_peer_gets_nothing(self):
        source = self.make_file(50_000)
        send_file(self.sender, self.receiver.device_id, source)
        offer = json.loads(self.wire[0])
        self.receiver.verify_peer = lambda _id, _cert: False  # certificate not pinned
        destination = os.path.join(self.tmp.name, "downloads")
        self.assertIsNone(receive_file(self.receiver, self.link_to_sender, offer, destination))
        self.assertEqual(os.listdir(destination), [])
        self.assertTrue(self.got_progress[-1]["error"])

    def test_cannot_send_to_unpaired_device(self):
        self.sender._active[self.receiver.device_id]["paired"] = False
        self.assertFalse(send_file(self.sender, self.receiver.device_id, self.make_file(10)))

    def test_missing_file_is_refused(self):
        self.assertFalse(send_file(self.sender, self.receiver.device_id, "/nonexistent/file"))


class ReceiveSafetyTests(unittest.TestCase):
    def test_filenames_are_sanitised(self):
        self.assertEqual(clean_filename("../../etc/passwd"), "passwd")
        self.assertEqual(clean_filename(".."), "received-file")
        self.assertEqual(clean_filename("a\x00b.txt"), "ab.txt")

    def test_existing_files_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "a.txt"), "w").close()
            self.assertEqual(os.path.basename(unique_path(folder, "a.txt")), "a (1).txt")

    def test_rejects_absurd_payloads(self):
        daemon = FakeDaemon()
        link = {"id": "x", "name": "x", "address": "127.0.0.1"}
        for size, port in ((-1, 5000), (3 * 1024 ** 3, 5000), (10, 0), (10, True)):
            bad = packet("kdeconnect.share.request", {"filename": "f"}, payloadSize=size,
                         payloadTransferInfo={"port": port})
            self.assertIsNone(receive_file(daemon, link, bad, "/tmp"))


class TextAndLinkTests(unittest.TestCase):
    def test_text_goes_to_clipboard_and_notifies(self):
        daemon = FakeDaemon()
        Share().handle_packet(daemon, {"id": "d", "name": "Pixel"}, packet("kdeconnect.share.request", {"text": "hello"}))
        self.assertEqual(daemon.clipboard, ["hello"])
        self.assertEqual(daemon.notifications[0]["summary"], "Text from Pixel")

    def test_link_waits_for_a_click_by_default(self):
        daemon = FakeDaemon()
        Share().handle_packet(daemon, {"id": "d", "name": "Pixel"}, packet("kdeconnect.share.request", {"url": "https://example.org"}))
        self.assertEqual(daemon.urls, [])
        daemon.notifications[0]["actions"][0][2]()
        self.assertEqual(daemon.urls, ["https://example.org"])

    def test_non_web_links_are_ignored(self):
        daemon = FakeDaemon(settings={"share": {"auto_open_links": True}})
        Share().handle_packet(daemon, {"id": "d", "name": "P"}, packet("kdeconnect.share.request", {"url": "file:///etc/passwd"}))
        self.assertEqual((daemon.urls, daemon.notifications), ([], []))


class PacketTests(unittest.TestCase):
    def test_payload_fields_sit_beside_the_body(self):
        data = json.loads(json_packet("kdeconnect.share.request", {"filename": "a"}, payload_size=5, payload_port=1740))
        self.assertEqual((data["payloadSize"], data["payloadTransferInfo"]), (5, {"port": 1740}))
        self.assertNotIn("payloadSize", json.loads(json_packet("kdeconnect.ping", {})))


if __name__ == "__main__":
    unittest.main()
