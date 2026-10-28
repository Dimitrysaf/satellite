"""Album art both ways, sending text and links, satellite-cli and the Nemo action."""

import io
import os
import tempfile
import unittest
from unittest import mock

from satellite import cli
from satellite.plugins import mpris
from satellite.plugins.mpriscontrol import Plugin as MediaControl, now_playing
from satellite.plugins.share import is_link, send_text
from tests.fakes import LINK, FakeDaemon, packet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PhoneAlbumArtTests(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": self.cache.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.cache.cleanup()

    def test_art_is_asked_for_once_then_offered_to_the_desktop(self):
        daemon = FakeDaemon(devices={"dev1": {"id": "dev1"}})
        downloaded = []

        def download(_d, _l, _p, folder, name):
            path = os.path.join(folder, name)
            open(path, "wb").close()
            downloaded.append(path)
            return path

        plugin = mpris.Plugin(downloader=download)
        playing = packet("kdeconnect.mpris", {"player": "Spotify", "title": "Song", "albumArtUrl": "file:///a.jpg"})
        plugin.handle_packet(daemon, LINK, playing)
        plugin.handle_packet(daemon, LINK, playing)  # not asked twice
        self.assertEqual([s[3] for s in daemon.sent],
                         [{"player": "Spotify", "albumArtUrl": "file:///a.jpg"}])
        art = dict(packet("kdeconnect.mpris", {"player": "Spotify", "transferringAlbumArt": True,
                                               "albumArtUrl": "file:///a.jpg"}), payloadSize=10)
        with mock.patch("threading.Thread") as thread:
            thread.side_effect = lambda target, args, daemon: mock.Mock(start=lambda: target(*args))
            plugin.handle_packet(daemon, LINK, art)
        self.assertEqual(daemon._devices["dev1"]["plugin_data"]["mpris_art"],
                         {"url": "file:///a.jpg", "path": downloaded[0]})
        self.assertEqual(daemon._devices["dev1"]["plugin_data"]["mpris"]["body"]["title"], "Song")

    def test_switched_off(self):
        daemon = FakeDaemon(settings={"mpris": {"album_art": False}}, devices={"dev1": {"id": "dev1"}})
        mpris.Plugin().handle_packet(daemon, LINK, packet("kdeconnect.mpris", {"albumArtUrl": "file:///a.jpg"}))
        self.assertEqual(daemon.sent, [])


class DesktopAlbumArtTests(unittest.TestCase):
    def test_only_the_current_local_art_is_sent(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg") as cover:
            cover.write(b"JPEGDATA")
            cover.flush()
            url = "file://" + cover.name
            players = mock.Mock()
            players.bus_name.return_value = "org.mpris.MediaPlayer2.vlc"
            players.state.return_value = {"Metadata": {"mpris:artUrl": url}}
            plugin, daemon = MediaControl(players), FakeDaemon()
            self.assertEqual(now_playing("VLC", players.state())["albumArtUrl"], url)
            with mock.patch("satellite.plugins.share.send_bytes") as send:
                plugin.handle_packet(daemon, LINK, packet("kdeconnect.mpris.request",
                                                          {"player": "VLC", "albumArtUrl": url}))
                plugin.handle_packet(daemon, LINK, packet("kdeconnect.mpris.request",
                                                          {"player": "VLC", "albumArtUrl": "file:///etc/passwd"}))
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args[0][3]["transferringAlbumArt"], True)
            self.assertEqual(send.call_args[0][4], b"JPEGDATA")


class SendTextTests(unittest.TestCase):
    def test_links_open_and_text_is_copied(self):
        self.assertTrue(is_link("https://linuxmint.com/download.php"))
        self.assertTrue(is_link("geo:37.97,23.72"))
        self.assertFalse(is_link("see https://x.org"))
        daemon = FakeDaemon()
        send_text(daemon, "dev1", " https://lwn.net ")
        send_text(daemon, "dev1", "milk, eggs")
        self.assertFalse(send_text(daemon, "dev1", "   "))
        self.assertEqual([s[3] for s in daemon.sent], [{"url": "https://lwn.net"}, {"text": "milk, eggs"}])


DEVICES = [{"id": "a" * 32, "name": "Pixel 7", "connected": True, "paired": True},
           {"id": "b" * 32, "name": "Galaxy Tab", "connected": False, "paired": True}]


class CliTests(unittest.TestCase):
    def test_device_choice(self):
        self.assertEqual(cli.find_device(DEVICES)["name"], "Pixel 7")  # the only connected one
        self.assertEqual(cli.find_device(DEVICES, "pixel 7")["id"], "a" * 32)
        for wanted in ("Nokia", "Galaxy Tab"):
            with self.assertRaises(cli.CliError) as error:
                cli.find_device(DEVICES, wanted)
            self.assertEqual(error.exception.code, cli.NO_DEVICE)
        both = [dict(d, connected=True) for d in DEVICES]
        with self.assertRaises(cli.CliError):
            cli.find_device(both)

    def test_commands_reach_satellite(self):
        client = mock.Mock()
        client.devices.return_value = DEVICES
        client.send_text.return_value = True
        self.assertEqual(cli.run(cli.parse(["text", "https://lwn.net"]), client), cli.OK)
        client.send_text.assert_called_once_with("a" * 32, "https://lwn.net")
        out = io.StringIO()
        cli.run(cli.parse(["devices"]), client, out)
        self.assertIn("Pixel 7  (connected)", out.getvalue())
        client.ping.return_value = False
        with self.assertRaises(cli.CliError):
            cli.run(cli.parse(["ping"]), client)

    def test_missing_files_are_refused_before_sending(self):
        client = mock.Mock()
        client.devices.return_value = DEVICES
        with self.assertRaises(cli.CliError):
            cli.run(cli.parse(["send", "/nonexistent/file.pdf"]), client)
        client.send_files.assert_not_called()


class NemoActionTests(unittest.TestCase):
    def test_action_file(self):
        from gi.repository import GLib
        keys = GLib.KeyFile()
        keys.load_from_file(os.path.join(ROOT, "data", "satellite-send.nemo_action.in"), GLib.KeyFileFlags.NONE)
        self.assertEqual(keys.get_string("Nemo Action", "Exec"), "satellite-cli --pick send %F")
        self.assertEqual(keys.get_string("Nemo Action", "Extensions"), "nodirs;")
        self.assertIn("satellite-cli", keys.get_string("Nemo Action", "Dependencies"))


if __name__ == "__main__":
    unittest.main()
