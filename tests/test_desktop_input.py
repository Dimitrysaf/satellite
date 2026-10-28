"""Input backends: what each one is asked to run for a given action."""

import os
import unittest
from unittest import mock

from satellite import desktop_input
from satellite.desktop_input import PortalBackend, XdotoolBackend, YdotoolBackend, char_keysym
from satellite.plugins.mousepad import DesktopInput


def recorder():
    calls = []
    return calls, lambda argv, **_kwargs: calls.append(argv[1:])


class XdotoolTests(unittest.TestCase):
    def test_negative_moves_are_not_read_as_options(self):
        calls, runner = recorder()
        XdotoolBackend("xdotool", runner).move(-5, 3)
        self.assertEqual(calls, [["mousemove_relative", "--sync", "--", "-5", "3"]])


class YdotoolTests(unittest.TestCase):
    def run_body(self, body, left_handed=False):
        calls, runner = recorder()
        DesktopInput(backend=YdotoolBackend("ydotool", runner)).apply(body, left_handed)
        return calls

    def test_move_and_click(self):
        self.assertEqual(self.run_body({"dx": 4, "dy": -2}), [["mousemove", "-x", "4", "-y", "-2"]])
        self.assertEqual(self.run_body({"rightclick": True})[0][-1], "0xC1")
        self.assertEqual(self.run_body({"singleclick": True}, left_handed=True)[0][-1], "0xC1")

    def test_ctrl_c_presses_and_releases_in_order(self):
        self.assertEqual(self.run_body({"key": "c", "ctrl": True}),
                         [["key", "29:1", "46:1", "46:0", "29:0"]])

    def test_text_is_typed(self):
        self.assertEqual(self.run_body({"key": "hello"}), [["type", "--", "hello"]])


class PortalKeysymTests(unittest.TestCase):
    def test_latin1_and_unicode_keysyms(self):
        self.assertEqual(char_keysym("a"), 0x61)
        self.assertEqual(char_keysym("é"), 0xe9)
        self.assertEqual(char_keysym("λ"), 0x01000000 | 0x3bb)

    def test_key_chord_order(self):
        sent = []
        backend = PortalBackend()
        backend._keysym = lambda sym, state: sent.append((sym, state))
        backend.key(["ctrl", "c"])
        self.assertEqual(sent, [(0xffe3, 1), (0x63, 1), (0x63, 0), (0xffe3, 0)])


class BackendChoiceTests(unittest.TestCase):
    def pick(self, env, portal=False, tools=()):
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(PortalBackend, "available", classmethod(lambda cls: portal)), \
                mock.patch.object(desktop_input.shutil, "which",
                                  lambda name: "/usr/bin/" + name if name in tools else None):
            backend = desktop_input.pick_backend()
        return backend.name if backend else None

    def test_x11_uses_xdotool(self):
        self.assertEqual(self.pick({"XDG_SESSION_TYPE": "x11"}, tools=("xdotool",)), "xdotool")

    def test_wayland_prefers_the_portal(self):
        env = {"WAYLAND_DISPLAY": "wayland-0"}
        self.assertEqual(self.pick(env, portal=True, tools=("xdotool", "ydotool")), "portal")
        self.assertEqual(self.pick(env, tools=("xdotool", "ydotool")), "ydotool")
        self.assertIsNone(self.pick(env, tools=("xdotool",)))


if __name__ == "__main__":
    unittest.main()
