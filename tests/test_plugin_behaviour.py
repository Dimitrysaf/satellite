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
from satellite.plugins.telephony import Plugin as Telephony
from tests.fakes import LINK, FakeDaemon, packet


class PingTests(unittest.TestCase):
    def test_incoming_ping_notifies(self):
        daemon = FakeDaemon(devices={"dev1": {"id": "dev1"}})
        self.assertTrue(Ping.handle_packet(daemon, LINK, packet("kdeconnect.ping", {"message": "hi"})))
        self.assertEqual(daemon.notifications[0]["summary"], "Ping from Pixel 7")
        self.assertEqual(daemon._devices["dev1"]["last_ping"], "hi")

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

    def test_silent_and_cancel_do_not_show(self):
        daemon, plugin = FakeDaemon(), Notifications()
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


class RingTests(unittest.TestCase):
    def test_request_starts_then_stops_ringing(self):
        stopped = threading.Event()
        plugin = Ring(sound_command=lambda: None)
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
        self.assertEqual(text, "5G · signal 4/4")

    def test_lock_status(self):
        device = {"plugin_data": {"lock": {"body": {"isLocked": True}}}}
        self.assertEqual(Lock().status(device)[1], "Device is locked")


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


if __name__ == "__main__":
    unittest.main()
