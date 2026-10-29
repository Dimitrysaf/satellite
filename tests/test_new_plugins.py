"""Presenter, remote input, volume, media control, browsing and screensaver."""

import json
import os
import tempfile
import unittest
from unittest import mock

from satellite.plugins.mpriscontrol import now_playing
from satellite.plugins.presenter import Plugin as Presenter, PointerPosition
from satellite.plugins.remoteinput import key_body
from satellite.plugins.screensaver_inhibit import Plugin as ScreensaverInhibit
from satellite.plugins.sftp import Plugin as Sftp, sshfs_command
from satellite.plugins.systemvolume import Plugin as SystemVolume, parse_sinks
from tests.fakes import LINK, FakeDaemon, packet


class PresenterTests(unittest.TestCase):
    def test_moves_like_kde_connect(self):
        position = PointerPosition()
        self.assertEqual(position.move(0.1, 0.1, ratio=16 / 9), (0.6, 0.5 + 0.1 * 16 / 9))
        self.assertEqual(position.move(5, -5, ratio=1), (1.0, 0.0))  # clamped to the screen

    def test_overlay_styles_get_positions_and_stop_resets(self):
        shown = []
        plugin = Presenter(display=lambda position, settings: shown.append(position))
        daemon = FakeDaemon(settings={"presenter": {"style": "dot"}})
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.presenter", {"dx": 0.1, "dy": 0}))
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.presenter", {"stop": True}))
        self.assertAlmostEqual(shown[0][0], 0.6)
        self.assertIsNone(shown[1])
        self.assertEqual(plugin._position.x, 0.5)

    def test_cursor_mode_keeps_sub_pixel_movement(self):
        moves = []
        plugin = Presenter(display=lambda *_a: None, mover=lambda dx, dy: moves.append((dx, dy)))
        plugin._screen_size = lambda: (1000, 500)
        daemon = FakeDaemon(settings={"presenter": {"style": "cursor"}})
        for _ in range(4):  # 0.3 px each: nothing until a whole pixel has built up
            plugin.handle_packet(daemon, LINK, packet("kdeconnect.presenter", {"dx": 0.0003, "dy": 0}))
        self.assertEqual(moves, [(1, 0)])


class RemoteInputTests(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(key_body("a", "a")["key"], "a")
        self.assertFalse(key_body("A", "A", shift=True)["shift"])  # shift is in the letter
        self.assertTrue(key_body("C", "C", ctrl=True, shift=True)["shift"])  # Ctrl+Shift+C
        self.assertEqual(key_body("Return", "\r")["specialKey"], 12)
        self.assertEqual(key_body("F5", "")["specialKey"], 25)
        self.assertTrue(key_body("ISO_Left_Tab", "")["shift"])
        self.assertIsNone(key_body("Shift_L", ""))


PACTL_SINKS = json.dumps([
    {"name": "speakers", "description": "Speakers", "mute": False,
     "volume": {"front-left": {"value": 32768}, "front-right": {"value": 40000}}},
    {"name": "hdmi", "description": "HDMI", "mute": True, "volume": {"mono": {"value": 65536}}},
])


class SystemVolumeTests(unittest.TestCase):
    def make(self, listing=PACTL_SINKS, default="speakers"):
        calls = []

        def pactl(*arguments):
            calls.append(arguments)
            if arguments == ("get-default-sink",):
                return default + "\n"
            if arguments[:3] == ("-f", "json", "list"):
                return listing
            return ""
        return SystemVolume(pactl=pactl, watcher=False), calls

    def test_sink_list(self):
        sinks = parse_sinks(PACTL_SINKS, "speakers")
        self.assertEqual(sinks[0], {"name": "speakers", "description": "Speakers", "muted": False,
                                    "volume": 40000, "maxVolume": 65536, "enabled": True})
        self.assertTrue(sinks[1]["muted"])

    def test_requests_change_the_desktop(self):
        plugin, calls = self.make()
        daemon = FakeDaemon()
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.systemvolume.request", {"requestSinks": True}))
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.systemvolume.request",
                                                  {"name": "hdmi", "volume": 30000, "muted": False, "enabled": True}))
        self.assertEqual(len(daemon.sent[0][3]["sinkList"]), 2)
        self.assertIn(("set-sink-volume", "hdmi", "30000"), calls)
        self.assertIn(("set-sink-mute", "hdmi", "0"), calls)
        self.assertIn(("set-default-sink", "hdmi"), calls)

    def test_desktop_changes_are_pushed(self):
        plugin, _calls = self.make()
        daemon = FakeDaemon(devices={"dev1": {"id": "dev1", "connected": True}})
        plugin.on_connected(daemon, "dev1")
        plugin._send_sink_list(daemon, "dev1")
        plugin._pactl = lambda *a: "hdmi" if a == ("get-default-sink",) else PACTL_SINKS
        plugin.push_changes()
        self.assertEqual([body for *_x, body in daemon.sent[1:]],
                         [{"enabled": False, "name": "speakers"}, {"enabled": True, "name": "hdmi"}])


class MediaControlTests(unittest.TestCase):
    def test_now_playing_body(self):
        body = now_playing("VLC", {
            "PlaybackStatus": "Playing", "Volume": 0.42, "Position": 5_000_000,
            "Metadata": {"xesam:title": "Song", "xesam:artist": ["A", "B"], "mpris:length": 9_000_000},
        })
        self.assertEqual((body["title"], body["artist"], body["isPlaying"]), ("Song", "A, B", True))
        self.assertEqual((body["pos"], body["length"], body["volume"]), (5000, 9000, 42))


class SftpTests(unittest.TestCase):
    def test_sshfs_command(self):
        command = sshfs_command({"user": "kdeconnect", "port": 1739, "password": "x", "path": "/storage"},
                                "192.168.1.5", "/run/m")
        self.assertEqual(command[:3], ["sshfs", "kdeconnect@192.168.1.5:/storage", "/run/m"])
        self.assertIn("password_stdin", command)
        multi = sshfs_command({"user": "u", "port": 1, "multiPaths": ["/a"]}, "fe80::1", "/m")
        self.assertEqual(multi[1], "u@[fe80::1]:/")

    def test_mounts_as_a_drive_in_home_and_unmounts_on_disconnect(self):
        runs = []
        body = {"user": "u", "port": 1739, "password": "secret", "path": "/"}
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}):
            plugin = Sftp(runner=lambda argv, **kwargs: runs.append((argv, kwargs.get("input"))))
            daemon = FakeDaemon()
            plugin._mount(daemon, LINK, body)  # on connecting: mounted, not opened
            mount = os.path.join(home, "Satellite", "Pixel 7")
            self.assertEqual((runs[0][0][0], runs[0][0][2], runs[0][1]), ("sshfs", mount, "secret"))
            self.assertEqual(daemon.urls, [])
            with mock.patch("os.path.ismount", return_value=True):
                plugin.on_disconnected(daemon, LINK["id"])
            self.assertEqual(runs[1][0][1:], ["-u", mount])
            self.assertFalse(os.path.exists(os.path.join(home, "Satellite")))  # nothing left behind

    def test_browse_files_opens_the_drive(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}):
            plugin = Sftp(runner=lambda argv, **kwargs: None)
            daemon = FakeDaemon()
            plugin._open_when_mounted.add(LINK["id"])
            plugin._mount(daemon, LINK, {"user": "u", "port": 1739, "password": "x"})
            self.assertEqual(daemon.urls, ["file://" + os.path.join(home, "Satellite", "Pixel%207")])

    def test_device_error_is_shown_when_browsing(self):
        daemon = FakeDaemon()
        plugin = Sftp()
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.sftp", {"errorMessage": "No storage"}))
        self.assertEqual(daemon.notifications, [])  # the automatic mount on connecting stays quiet
        plugin._open_when_mounted.add(LINK["id"])
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.sftp", {"errorMessage": "No storage"}))
        self.assertEqual(daemon.notifications[0]["body"], "No storage")


class ScreensaverTests(unittest.TestCase):
    def test_inhibits_while_connected(self):
        log = []

        class Inhibitor:
            def inhibit(self):
                log.append("inhibit")
                return lambda: log.append("release")

        plugin = ScreensaverInhibit(Inhibitor())
        plugin.on_connected(None, "dev1")
        plugin.on_connected(None, "dev1")  # reconnect: still one inhibition
        plugin.on_disconnected(None, "dev1")
        plugin.on_disconnected(None, "dev1")
        self.assertEqual(log, ["inhibit", "release"])


if __name__ == "__main__":
    unittest.main()
