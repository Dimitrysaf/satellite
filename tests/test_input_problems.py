"""Sideways scrolling, pasting what ydotool can't type, and saying why input doesn't work."""

import os
import unittest
from unittest import mock

from satellite import desktop_input
from satellite.desktop_input import (InputProblem, PortalBackend, XdotoolBackend, YdotoolBackend,
                                     status_text)
from satellite.plugins.mousepad import DesktopInput
from satellite.plugins.presenter import Plugin as Presenter
from tests.fakes import LINK, FakeDaemon, packet


def recorder(clipboard=b"old"):
    calls = []

    def run(argv, **kwargs):
        calls.append((os.path.basename(argv[0]), argv[1:], kwargs.get("input")))
        return mock.Mock(returncode=0, stdout=clipboard)
    return calls, run


def ydotool(runner, wl_copy="/usr/bin/wl-copy"):
    return YdotoolBackend("ydotool", runner, wl_copy=wl_copy, wl_paste=wl_copy and "/usr/bin/wl-paste")


class SidewaysScrollTests(unittest.TestCase):
    BODY = {"scroll": True, "dx": 3, "dy": -2}

    def test_every_backend_scrolls_both_ways(self):
        calls, runner = recorder()
        DesktopInput(backend=XdotoolBackend("xdotool", runner)).apply(self.BODY)
        self.assertEqual([c[1][-1] for c in calls], ["5", "7"])  # down, right
        calls, runner = recorder()
        DesktopInput(backend=ydotool(runner)).apply(self.BODY)
        self.assertEqual([c[1] for c in calls], [["mousemove", "--wheel", "-x", "0", "-y", "-2"],
                                                 ["mousemove", "--wheel", "-x", "3", "-y", "0"]])
        portal, sent = PortalBackend(), []
        portal._notify = lambda method, signature, *values: sent.append(values)
        DesktopInput(backend=portal).apply(self.BODY)
        self.assertEqual(sent, [(0, 2), (1, 3)])  # the portal counts downwards

    def test_scrolling_does_not_move_the_pointer(self):
        calls, runner = recorder()
        DesktopInput(backend=XdotoolBackend("xdotool", runner)).apply({"scroll": True, "dx": 1, "dy": 0})
        self.assertEqual(calls, [("xdotool", ["click", "--repeat", "1", "--delay", "20", "7"], None)])


class YdotoolPasteTests(unittest.TestCase):
    def test_us_keyboard_text_is_typed(self):
        calls, runner = recorder()
        ydotool(runner).type_text("Hello, world!\n")
        self.assertEqual(calls, [("ydotool", ["type", "--", "Hello, world!\n"], None)])

    def test_other_letters_are_pasted_and_the_clipboard_comes_back(self):
        calls, runner = recorder()
        backend = ydotool(runner)
        with mock.patch("threading.Timer") as timer:
            backend.type_text("Καλημέρα")
        self.assertEqual([c[0] for c in calls], ["wl-paste", "wl-copy", "ydotool"])
        self.assertEqual(calls[1][2], "Καλημέρα".encode("utf-8"))
        self.assertEqual(calls[2][1], ["key", "29:1", "47:1", "47:0", "29:0"])  # Ctrl+V
        _delay, restore, (previous,) = timer.call_args[0]
        restore(previous)
        self.assertEqual(calls[-1][2], b"old")

    def test_without_wl_clipboard_it_says_what_to_install(self):
        calls, runner = recorder()
        with self.assertRaises(InputProblem) as problem:
            ydotool(runner, wl_copy="").type_text("é")
        self.assertIn("wl-clipboard", str(problem.exception))
        self.assertEqual(calls, [])


class ProblemReportTests(unittest.TestCase):
    def test_no_backend_is_reported_once(self):
        told = []
        source = DesktopInput(backend=False)
        source.on_problem = told.append
        with mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11"}, clear=True):
            for _ in range(3):
                self.assertFalse(source.apply({"dx": 1, "dy": 1}))
        self.assertEqual(told, ["Install xdotool: sudo apt install xdotool"])

    def test_failing_ydotool_points_at_its_service(self):
        told = []

        def broken(*_a, **_k):
            raise desktop_input.subprocess.CalledProcessError(1, "ydotool")
        source = DesktopInput(backend=ydotool(broken))
        source.on_problem = told.append
        source.move(3, 4)
        self.assertIn("ydotoold", told[0])

    def test_a_refused_portal_is_not_asked_again_at_once(self):
        portal, asked = PortalBackend(), []

        def refuse():
            asked.append(1)
            raise RuntimeError("Start was refused or cancelled")
        portal._start_session = refuse
        for _ in range(3):
            with self.assertRaises(InputProblem):
                portal._ensure_session()
        self.assertEqual(len(asked), 1)
        portal._refused_at -= PortalBackend.ASK_AGAIN_SECONDS
        with self.assertRaises(InputProblem):
            portal._ensure_session()
        self.assertEqual(len(asked), 2)

    def test_status_lines(self):
        self.assertEqual(status_text(XdotoolBackend("xdotool")), "Using xdotool (X11).")
        with mock.patch.object(YdotoolBackend, "service_running", staticmethod(lambda: False)):
            line = status_text(ydotool(None, wl_copy=""))
        self.assertIn("ydotoold service doesn't seem to be running", line)
        self.assertIn("wl-clipboard", line)
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True):
            self.assertIn("ydotool", status_text(None))


class PresenterWarningTests(unittest.TestCase):
    MOVE = packet("kdeconnect.presenter", {"dx": 0.1, "dy": 0})

    def test_slides_need_mouse_and_keyboard(self):
        daemon = FakeDaemon(settings={"presenter": {"style": "dot"}}, disabled={"mousepad"})
        plugin = Presenter(display=lambda *_a: None, limitation=lambda: None)
        plugin.handle_packet(daemon, LINK, self.MOVE)
        plugin.handle_packet(daemon, LINK, self.MOVE)
        self.assertEqual([n["summary"] for n in daemon.notifications], ["Slides won't change"])
        daemon.notifications[0]["actions"][0][2]()  # "Turn on Mouse and keyboard"
        self.assertTrue(daemon.plugins.is_enabled_for("dev1", "mousepad"))

    def test_pointer_that_may_hide_offers_the_mouse_pointer(self):
        daemon = FakeDaemon(settings={"presenter": {"style": "spotlight", "speed": "fast"}})
        plugin = Presenter(display=lambda *_a: None, limitation=lambda: "no-layer-shell")
        plugin.handle_packet(daemon, LINK, self.MOVE)
        plugin.handle_packet(daemon, LINK, self.MOVE)
        self.assertEqual(len(daemon.notifications), 1)
        self.assertIn("layer-shell", daemon.notifications[0]["body"])
        daemon.notifications[0]["actions"][0][2]()
        self.assertEqual(daemon.plugins.settings["presenter"], {"style": "cursor", "speed": "fast"})

    def test_nothing_to_say_when_all_is_well(self):
        daemon = FakeDaemon(settings={"presenter": {"style": "dot"}})
        Presenter(display=lambda *_a: None, limitation=lambda: None).handle_packet(daemon, LINK, self.MOVE)
        self.assertEqual(daemon.notifications, [])


if __name__ == "__main__":
    unittest.main()
