"""Chat notifications, ones already on the phone, caller photos and choosing apps."""

import base64
import os
import tempfile
import unittest
from unittest import mock

from satellite.plugins.notifications import Plugin as Notifications, describe
from satellite.plugins.sendnotifications import KnownApps, Plugin as SendNotifications
from satellite.plugins.telephony import Plugin as Telephony, caller_photo
from tests.fakes import LINK, FakeDaemon, packet
from tests.test_plugin_behaviour import notification

CHAT = [{"sender": "Ann", "content": "Lunch?"}, {"sender": "Ann", "content": "Usual place"},
        {"sender": "Ben", "content": "I'm in"}]


def note(**fields):
    return packet("kdeconnect.notification", dict({"id": "n1", "appName": "Signal", "title": "Ann",
                                                   "text": "Lunch?", "isClearable": True}, **fields))


class ChatTests(unittest.TestCase):
    def test_one_to_one_chat(self):
        self.assertEqual(describe({"appName": "Signal", "conversation": CHAT[:2]}),
                         ("Signal", "Ann", "Lunch?\nUsual place", ""))

    def test_group_chat_names_everyone(self):
        _app, title, text, group = describe({"appName": "WhatsApp", "groupName": "Family", "conversation": CHAT})
        self.assertEqual((title, group), ("Family", "Family"))
        self.assertEqual(text, "Ann: Lunch?\nAnn: Usual place\nBen: I'm in")

    def test_only_the_latest_messages(self):
        many = [{"sender": "Ann", "content": str(n)} for n in range(10)]
        self.assertEqual(describe({"conversation": many})[2].split("\n"), [str(n) for n in range(4, 10)])

    def test_reply_names_the_group(self):
        daemon = FakeDaemon(devices={"dev1": {"id": "dev1"}})
        Notifications().handle_packet(daemon, LINK, note(groupName="Family", conversation=CHAT,
                                                         requestReplyId="r1"))
        shown = daemon.notifications[-1]
        self.assertEqual(shown["summary"], "Signal: Family")
        reply = next(action for action in shown["actions"] if action[0] == "reply")
        self.assertEqual(reply[1], "Reply in Family")
        reply[2]()
        self.assertEqual(daemon.prompts[0][0], "Reply in Family")


class ExistingNotificationTests(unittest.TestCase):
    """Ones already on the phone at connection go to the desktop too, quietly."""

    def test_already_there_comes_quietly(self):
        daemon = FakeDaemon()
        plugin = Notifications()
        plugin.handle_packet(daemon, LINK, note(silent=True))
        plugin.handle_packet(daemon, LINK, note(id="n2", title="Bob", text="Hi"))
        quiet, new = daemon.notifications
        self.assertEqual((quiet["urgency"], quiet["sound"]), (0, False))
        self.assertNotIn("urgency", new)
        self.assertNotIn("sound", new)

    def test_can_be_left_on_the_phone(self):
        daemon = FakeDaemon(settings={"notifications": {"show_existing": False}})
        Notifications().handle_packet(daemon, LINK, note(silent=True))
        self.assertEqual(daemon.notifications, [])

    def test_buttons_still_reach_the_phone(self):
        daemon = FakeDaemon()
        Notifications().handle_packet(daemon, LINK, note(silent=True, actions=["Mark as read"],
                                                         requestReplyId="r1"))
        actions = {key: callback for key, _label, callback in daemon.notifications[-1]["actions"]}
        actions["a0"]()
        self.assertEqual(daemon.sent[-1][3], {"key": "n1", "action": "Mark as read"})
        actions["reply"]()
        daemon.prompts[-1][1]("Yes")
        self.assertEqual(daemon.sent[-1][3], {"requestReplyId": "r1", "message": "Yes"})


class NotifierHintTests(unittest.TestCase):
    def test_quiet_and_low_urgency_hints(self):
        from satellite.notifier import Notifier
        notifier = Notifier.__new__(Notifier)
        notifier.app_name, notifier._callbacks, notifier._closed = "Satellite", {}, {}
        notifier._bus = mock.Mock()
        notifier._bus.call_sync.return_value.unpack.return_value = (7,)
        notifier.notify("Signal: Ann", "Lunch?", urgency=0, sound=False)
        hints = notifier._bus.call_sync.call_args[0][4].unpack()[6]
        self.assertEqual(hints, {"urgency": 0, "suppress-sound": True})


JPEG = b"\xff\xd8\xff\xe0" + b"photo" * 10


class CallerPhotoTests(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": self.cache.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.cache.cleanup()

    def ring(self, settings=None, **fields):
        daemon = FakeDaemon(settings={"telephony": settings or {}})
        body = dict({"event": "ringing", "contactName": "Ann"}, **fields)
        Telephony().handle_packet(daemon, LINK, packet("kdeconnect.telephony", body))
        return daemon.notifications[-1]["icon"]

    def test_photo_is_shown(self):
        icon = self.ring(phoneThumbnail=base64.b64encode(JPEG).decode())
        with open(icon, "rb") as stream:
            self.assertEqual(stream.read(), JPEG)

    def test_no_photo_or_a_bad_one(self):
        self.assertEqual(self.ring(), "call-start-symbolic")
        self.assertEqual(self.ring(phoneThumbnail="not base64!"), "call-start-symbolic")
        self.assertEqual(self.ring(phoneThumbnail=base64.b64encode(b"GIF89a").decode()), "call-start-symbolic")
        self.assertEqual(self.ring({"photo": False}, phoneThumbnail=base64.b64encode(JPEG).decode()),
                         "call-start-symbolic")
        self.assertIsNone(caller_photo({"phoneThumbnail": "A" * 1_000_000}))


class AppChoiceTests(unittest.TestCase):
    def test_apps_are_remembered_with_what_is_known(self):
        with tempfile.TemporaryDirectory() as config:
            apps = KnownApps(config)
            apps.see(notification(app_name="Firefox", hints={"desktop-entry": "firefox"}))
            apps.see(notification(app_name="Firefox", app_icon="firefox"))
            apps.see(notification(app_name="anydesk"))
            self.assertEqual(KnownApps(config).all(), {
                "anydesk": {"icon": "", "desktop": ""},
                "Firefox": {"icon": "firefox", "desktop": "firefox"},
            })

    def test_switched_off_apps_stay_on_this_computer(self):
        with tempfile.TemporaryDirectory() as config:
            daemon = FakeDaemon(settings={"sendnotifications": {"muted_apps": ["Update Manager"]}},
                                devices={"dev1": {"id": "dev1", "connected": True}})
            daemon.config_dir = config
            plugin = SendNotifications(monitor_factory=None, icon_loader=lambda _n: None)
            plugin._daemon = daemon
            plugin.forward(notification(app_name="Update Manager", summary="Updates available"))
            plugin.forward(notification(app_name="Firefox"))
            self.assertEqual([s[3]["appName"] for s in daemon.sent], ["Firefox"])
            self.assertEqual(set(plugin.known_apps(daemon).all()), {"Firefox", "Update Manager"})


if __name__ == "__main__":
    unittest.main()
