"""Behaviour of the plugins that act on the desktop."""

import threading
import unittest

from satellite.plugins.battery import Plugin as Battery
from satellite.plugins.connectivity_report import Plugin as Connectivity
from satellite.plugins.lock import Plugin as Lock
from satellite.plugins.mousepad import DesktopInput
from satellite.plugins.notifications import Plugin as Notifications
from satellite.plugins.pausemusic import Plugin as PauseMusic
from satellite.plugins.ping import Plugin as Ping
from satellite.plugins.ring import Plugin as Ring
from satellite.plugins.ring import find_sound_command
from satellite.plugins.sendnotifications import Plugin as SendNotifications
from satellite.plugins.telephony import Plugin as Telephony
from tests.fakes import LINK, FakeDaemon, packet


class PingTests(unittest.TestCase):
    def test_incoming_ping_notifies(self):
        daemon = FakeDaemon(devices={"dev1": {"id": "dev1"}})
        self.assertTrue(Ping.handle_packet(daemon, LINK, packet("kdeconnect.ping", {"message": "hi"})))
        self.assertEqual(daemon.notifications[0]["summary"], "Ping from Pixel 7")
        self.assertEqual(daemon.notifications[0]["body"], "hi")

    def test_notification_can_be_turned_off(self):
        daemon = FakeDaemon(settings={"ping": {"notify": False}}, devices={"dev1": {"id": "dev1"}})
        Ping.handle_packet(daemon, LINK, packet("kdeconnect.ping"))
        self.assertEqual(daemon.notifications, [])


class NotificationsTests(unittest.TestCase):
    def test_shows_phone_notification_and_replaces_updates(self):
        daemon, plugin = FakeDaemon(), Notifications()
        body = {"id": "n1", "appName": "Signal", "title": "Anna", "text": "Lunch?"}
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.notification", body))
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.notification", dict(body, text="Lunch at 1?")))
        self.assertEqual(daemon.notifications[0]["summary"], "Signal: Anna")
        self.assertEqual(daemon.notifications[1]["replaces"], 1)

    def test_silent_when_switched_off_and_cancel_do_not_show(self):
        daemon, plugin = FakeDaemon(settings={"notifications": {"show_existing": False}}), Notifications()
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.notification", {"id": "a", "silent": True}))
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.notification", {"id": "a", "isCancel": True}))
        self.assertEqual(daemon.notifications, [])

    def test_privacy_mode_hides_content(self):
        daemon = FakeDaemon(settings={"notifications": {"show_content": False}})
        Notifications().handle_packet(daemon, LINK, packet(
            "kdeconnect.notification", {"id": "a", "appName": "Bank", "title": "Code", "text": "123456"}))
        self.assertNotIn("123456", daemon.notifications[0]["body"])

    def test_action_button_sends_action_packet(self):
        daemon = FakeDaemon()
        Notifications().handle_packet(daemon, LINK, packet(
            "kdeconnect.notification", {"id": "n9", "appName": "Mail", "actions": ["Archive"]}))
        daemon.notifications[0]["actions"][0][2]()
        self.assertEqual(daemon.sent[-1][2:], ("kdeconnect.notification.action", {"key": "n9", "action": "Archive"}))

    def test_requests_existing_notifications_on_connect(self):
        daemon = FakeDaemon()
        Notifications().on_connected(daemon, "dev1")
        self.assertEqual(daemon.sent[0][2:], ("kdeconnect.notification.request", {"request": True}))


class TelephonyTests(unittest.TestCase):
    def setUp(self):
        self.daemon = FakeDaemon(devices={"dev1": {"id": "dev1"}})
        self.plugin = Telephony()

    def test_ringing_has_silence_button(self):
        self.plugin.handle_packet(self.daemon, LINK, packet(
            "kdeconnect.telephony", {"event": "ringing", "contactName": "Mum", "phoneNumber": "1"}))
        note = self.daemon.notifications[0]
        self.assertEqual(note["body"], "Mum")
        note["actions"][0][2]()
        self.assertEqual(self.daemon.sent[-1][2], "kdeconnect.telephony.request_mute")

    def test_cancel_replaces_the_ringing_notification(self):
        self.plugin.handle_packet(self.daemon, LINK, packet("kdeconnect.telephony", {"event": "ringing"}))
        self.plugin.handle_packet(self.daemon, LINK, packet("kdeconnect.telephony", {"event": "ringing", "isCancel": True}))
        self.assertEqual(self.daemon.notifications[1]["summary"], "Call ended")
        self.assertEqual(self.daemon.notifications[1]["replaces"], 1)


class LockTests(unittest.TestCase):
    def make(self, locked="no", allow_unlock=False):
        calls = []

        def loginctl(*args):
            calls.append(args)
            return locked + "\n" if args[0] == "show-session" else ""

        daemon = FakeDaemon(settings={"lock": {"allow_unlock": allow_unlock}})
        return Lock(loginctl), daemon, calls

    def test_set_locked_locks_and_reports(self):
        plugin, daemon, calls = self.make(locked="yes")
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.lock.request", {"setLocked": True}))
        self.assertTrue(any(c[0] == "lock-session" for c in calls))
        self.assertEqual(daemon.sent[-1][3], {"isLocked": True})

    def test_unlock_is_refused_unless_allowed(self):
        plugin, daemon, calls = self.make()
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.lock.request", {"setLocked": False}))
        self.assertFalse(any(c[0] == "unlock-session" for c in calls))
        plugin, daemon, calls = self.make(allow_unlock=True)
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.lock.request", {"setLocked": False}))
        self.assertTrue(any(c[0] == "unlock-session" for c in calls))

    def test_request_locked_reports_state(self):
        plugin, daemon, _ = self.make(locked="no")
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.lock.request", {"requestLocked": None}))
        self.assertEqual(daemon.sent[-1][3], {"isLocked": False})


class BatteryTests(unittest.TestCase):
    def test_sends_local_battery_once_until_it_changes(self):
        reading = [(80, False)]
        plugin = Battery(reader=lambda: reading[0])
        daemon = FakeDaemon()
        self.assertTrue(plugin._report(daemon, "dev1"))
        self.assertFalse(plugin._report(daemon, "dev1"))
        reading[0] = (14, False)
        self.assertTrue(plugin._report(daemon, "dev1"))
        self.assertEqual(daemon.sent[-1][3]["thresholdEvent"], 1)

    def test_desktop_without_battery_sends_nothing(self):
        self.assertFalse(Battery(reader=lambda: None)._report(FakeDaemon(), "dev1"))

    def test_warns_once_when_phone_battery_is_low(self):
        plugin = Battery(reader=lambda: None)
        daemon = FakeDaemon(devices={"dev1": {"id": "dev1"}})
        for charge in (20, 14, 13, 12):
            plugin.handle_packet(daemon, LINK, packet("kdeconnect.battery", {"currentCharge": charge, "isCharging": False}))
        self.assertEqual(len(daemon.notifications), 1)
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.battery", {"currentCharge": 90, "isCharging": True}))
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.battery", {"currentCharge": 10, "isCharging": False}))
        self.assertEqual(len(daemon.notifications), 2)


class FakePlayers:
    def __init__(self):
        self.running, self.log = ["org.mpris.MediaPlayer2.vlc"], []

    def playing(self):
        return list(self.running)

    def call(self, name, method):
        self.log.append((name, method))
        return True


class PauseMusicTests(unittest.TestCase):
    def test_pauses_on_ring_and_resumes_after(self):
        players, daemon = FakePlayers(), FakeDaemon()
        plugin = PauseMusic(players)
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.telephony", {"event": "ringing"}))
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.telephony", {"event": "ringing", "isCancel": True}))
        self.assertEqual([m for _n, m in players.log], ["Pause", "Play"])

    def test_resume_can_be_disabled(self):
        players = FakePlayers()
        plugin = PauseMusic(players)
        daemon = FakeDaemon(settings={"pausemusic": {"resume": False}})
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.telephony", {"event": "ringing"}))
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.telephony", {"isCancel": True}))
        self.assertEqual([m for _n, m in players.log], ["Pause"])


class FakeSound:
    def __init__(self, muted=False):
        self.muted, self.log = muted, []

    def is_muted(self):
        return self.muted

    def set_muted(self, muted):
        self.muted = muted
        self.log.append(muted)
        return True


class PauseMusicSettingsTests(unittest.TestCase):
    def ring_and_hang_up(self, settings, sound):
        players = FakePlayers()
        plugin = PauseMusic(players, sound)
        daemon = FakeDaemon(settings={"pausemusic": settings})
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.telephony", {"event": "ringing"}))
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.telephony", {"isCancel": True}))
        return [m for _n, m in players.log]

    def test_mute_only_mutes_and_restores_sound(self):
        sound = FakeSound()
        self.assertEqual(self.ring_and_hang_up({"pause": False, "mute": True}, sound), [])
        self.assertEqual(sound.log, [True, False])

    def test_already_muted_sound_stays_muted(self):
        sound = FakeSound(muted=True)
        self.ring_and_hang_up({"mute": True}, sound)
        self.assertEqual(sound.log, [])


class RingTests(unittest.TestCase):
    def test_request_starts_then_stops_ringing(self):
        stopped = threading.Event()
        plugin = Ring(sound_command=lambda _sound: None)
        daemon = FakeDaemon(devices={"dev1": {"id": "dev1"}})
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.findmyphone.request"))
        self.assertTrue(daemon._devices["dev1"]["ring_requested"])
        self.assertIn("Pixel 7", daemon.notifications[0]["summary"])
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.findmyphone.request"))
        self.assertFalse(daemon._devices["dev1"]["ring_requested"])
        stopped.set()


class StatusTests(unittest.TestCase):
    def test_connectivity_status(self):
        device = {"plugin_data": {"connectivity_report": {"body": {
            "signalStrengths": {"1": {"networkType": "5G", "signalStrength": 4}}}}}}
        icon, text = Connectivity().status(device)
        self.assertIn("excellent", icon)
        self.assertEqual(text, "5G Excellent")

    def test_lock_status(self):
        device = {"plugin_data": {"lock": {"body": {"isLocked": True}}}}
        self.assertEqual(Lock().status(device)[1], "Locked")


class DesktopInputTests(unittest.TestCase):
    def run_input(self, body, left_handed=False):
        calls = []
        desktop = DesktopInput(xdotool="/usr/bin/xdotool", runner=lambda argv, **_k: calls.append(argv[1:]))
        self.assertTrue(desktop.apply(body, left_handed))
        return calls

    def test_ctrl_c_is_a_chord_not_typed_text(self):
        self.assertEqual(self.run_input({"key": "c", "ctrl": True}), [["key", "ctrl+c"]])

    def test_plain_text_is_typed(self):
        self.assertEqual(self.run_input({"key": "hello"})[0][0], "type")

    def test_enter_sends_return(self):
        self.assertEqual(self.run_input({"specialKey": 12}), [["key", "Return"]])

    def test_left_handed_swaps_buttons(self):
        self.assertEqual(self.run_input({"singleclick": True}, True), [["click", "3"]])
        self.assertEqual(self.run_input({"rightclick": True}, True), [["click", "1"]])

    def test_unavailable_without_xdotool(self):
        self.assertFalse(DesktopInput(xdotool="", runner=None).apply({"singleclick": True}))


class RingSoundTests(unittest.TestCase):
    def test_missing_custom_sound_falls_back_to_default(self):
        self.assertEqual(find_sound_command("/nonexistent.oga"), find_sound_command())


def notification(**fields):
    base = {"app_name": "Firefox", "replaces_id": 0, "app_icon": "", "summary": "Done",
            "body": "report.pdf", "actions": [], "hints": {}, "timeout": -1}
    base.update(fields)
    return base


class SendNotificationsTests(unittest.TestCase):
    def forward(self, item, settings=None, icon=None):
        daemon = FakeDaemon(
            settings={"sendnotifications": settings or {}},
            devices={"dev1": {"id": "dev1", "connected": True}})
        plugin = SendNotifications(monitor_factory=None, icon_loader=lambda _n: icon)
        plugin._daemon = daemon
        plugin.forward(item)
        return [body for _p, _d, _t, body in daemon.sent]

    def test_forwards_title_and_body(self):
        body = self.forward(notification())[0]
        self.assertEqual((body["title"], body["text"], body["ticker"]),
                         ("Done", "report.pdf", "Done: report.pdf"))

    def test_body_can_be_left_out(self):
        body = self.forward(notification(), {"include_body": False})[0]
        self.assertNotIn("text", body)
        self.assertEqual(body["ticker"], "Done")

    def test_filters(self):
        self.assertEqual(self.forward(notification(timeout=5000), {"persistent": True}), [])
        low = notification(hints={"urgency": 0})
        self.assertEqual(self.forward(low, {"min_urgency": 1}), [])
        self.assertEqual(self.forward(notification(summary="")), [])

    def test_own_notifications_are_not_echoed(self):
        self.assertEqual(self.forward(notification(app_name="Satellite")), [])


if __name__ == "__main__":
    unittest.main()
