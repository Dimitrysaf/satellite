"""Devices' clipboards on this computer, and what clipboard histories are told."""

import hashlib
import unittest
from unittest import mock

from satellite.phone_service import PhoneService, device_summary
from satellite.ui import clipboard_sync
from tests.fakes import FakeDaemon

PHONE = {"id": "dev1", "name": "Pixel", "connected": True, "paired": True,
         "available_plugins": [{"id": "clipboard", "enabled": True}],
         "plugin_data": {"clipboard": {"id": 1, "body": {"content": "from the phone"}}}}


class FakeClipboard:
    def __init__(self, text=None):
        self.text = text

    def set_text(self, text, _length):
        self.text = text

    def wait_for_text(self):
        return self.text


def sync(daemon, clipboard):
    with mock.patch.object(clipboard_sync.Gtk.Clipboard, "get", return_value=clipboard):
        return clipboard_sync.ClipboardSync(daemon)


class ClipboardSyncTests(unittest.TestCase):
    def test_a_device_copy_is_announced_once(self):
        clipboard = FakeClipboard()
        clipboards = sync(FakeDaemon(), clipboard)
        received = []
        clipboards.on_received = lambda device, text: received.append((device["name"], text))
        clipboards.on_devices([PHONE])
        clipboards.on_devices([PHONE])
        self.assertEqual(clipboard.text, "from the phone")
        self.assertEqual(received, [("Pixel", "from the phone")])

    def test_sending_keeps_this_computers_clipboard_from_following(self):
        daemon = FakeDaemon()
        clipboards = sync(daemon, FakeClipboard("on the computer"))
        self.assertTrue(clipboards.send_to("dev1", "for the phone"))
        self.assertEqual(daemon.sent, [("clipboard", "dev1", "kdeconnect.clipboard", {"content": "for the phone"})])
        # The computer's clipboard counts as sent: it won't replace the phone's.
        self.assertEqual(clipboards._sent["dev1"], "on the computer")


class PhoneServiceTests(unittest.TestCase):
    def test_devices_say_whether_they_share_the_clipboard(self):
        self.assertTrue(device_summary(FakeDaemon(), PHONE)["clipboard"])
        self.assertFalse(device_summary(FakeDaemon(), dict(PHONE, available_plugins=[]))["clipboard"])

    def test_received_copies_are_announced_without_their_text(self):
        service = PhoneService(lambda: None)
        with mock.patch.object(service, "_emit") as emit:
            service._clipboard_received(PHONE, "secret text")
        emit.assert_called_once_with("ClipboardReceived", "(sss)",
                                     ("dev1", "Pixel", hashlib.sha256(b"secret text").hexdigest()))


if __name__ == "__main__":
    unittest.main()
