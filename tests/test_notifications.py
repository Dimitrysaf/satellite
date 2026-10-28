"""Phone notifications on the desktop: reply, two-way dismiss, icons, codes."""

import os
import tempfile
import unittest
from unittest import mock

from satellite.plugins.notifications import Plugin as Notifications, verification_code
from tests.fakes import LINK, FakeDaemon, packet


def note(**fields):
    return packet("kdeconnect.notification", dict({"id": "n1", "appName": "Signal", "title": "Ann",
                                                   "text": "Lunch?", "isClearable": True}, **fields))


class NotificationTests(unittest.TestCase):
    def show(self, settings=None, **fields):
        daemon = FakeDaemon(settings={"notifications": settings or {}})
        plugin = Notifications()
        plugin.handle_packet(daemon, LINK, note(**fields))
        return daemon, plugin

    def action(self, daemon, key):
        return next(cb for k, _label, cb in daemon.notifications[-1]["actions"] if k == key)

    def test_reply_goes_back_to_the_phone(self):
        daemon, _plugin = self.show(requestReplyId="r-9")
        self.action(daemon, "reply")()
        _title, send = daemon.prompts[0]
        send("On my way")
        self.assertEqual(daemon.sent[-1][2:], ("kdeconnect.notification.reply",
                                               {"requestReplyId": "r-9", "message": "On my way"}))

    def test_no_reply_without_reply_id_or_when_switched_off(self):
        daemon, _plugin = self.show()
        self.assertNotIn("reply", [k for k, _l, _c in daemon.notifications[-1]["actions"]])
        daemon, _plugin = self.show({"reply": False}, requestReplyId="r-9")
        self.assertNotIn("reply", [k for k, _l, _c in daemon.notifications[-1]["actions"]])

    def test_dismissed_here_is_dismissed_on_the_phone(self):
        daemon, _plugin = self.show()
        daemon.notifications[-1]["on_closed"](2)  # the user closed it
        self.assertEqual(daemon.sent[-1][2:], ("kdeconnect.notification.request", {"cancel": "n1"}))

    def test_expired_or_unclearable_is_not_dismissed_on_the_phone(self):
        daemon, _plugin = self.show()
        daemon.notifications[-1]["on_closed"](1)  # timed out
        daemon, _plugin = self.show(isClearable=False)
        daemon.notifications[-1]["on_closed"](2)
        self.assertEqual(daemon.sent, [])
        daemon, _plugin = self.show({"sync_dismiss": False})
        daemon.notifications[-1]["on_closed"](2)
        self.assertEqual(daemon.sent, [])

    def test_dismissed_on_the_phone_closes_it_here(self):
        daemon, plugin = self.show()
        plugin.handle_packet(daemon, LINK, note(isCancel=True))
        self.assertEqual(daemon.closed, [1])

    def test_icon_is_downloaded_once_and_shown(self):
        with tempfile.TemporaryDirectory() as cache, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
            downloads = []

            def download(_daemon, _link, _packet, folder, name):
                downloads.append(name)
                path = os.path.join(folder, name)
                open(path, "wb").close()
                return path

            daemon = FakeDaemon()
            plugin = Notifications(downloader=download)
            icon = dict(payloadHash="a1b2c3d4e5f6", payloadSize=10)
            with mock.patch("threading.Thread") as thread:
                thread.side_effect = lambda target, args, daemon: mock.Mock(start=lambda: target(*args))
                plugin.handle_packet(daemon, LINK, dict(note(payloadHash="a1b2c3d4e5f6"), payloadSize=10))
            plugin.handle_packet(daemon, LINK, dict(note(id="n2", **icon), payloadSize=10))
            self.assertEqual(downloads, ["a1b2c3d4e5f6.png"])
            self.assertTrue(daemon.notifications[-1]["icon"].endswith("a1b2c3d4e5f6.png"))

    def test_copy_verification_code(self):
        daemon, _plugin = self.show(title="Bank", text="Your verification code is 482913")
        self.action(daemon, "copy-code")()
        self.assertEqual(daemon.clipboard, ["482913"])
        self.assertIsNone(verification_code("Meet at 1930 near gate 4"))


if __name__ == "__main__":
    unittest.main()
