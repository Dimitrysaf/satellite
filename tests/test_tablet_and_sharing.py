"""The drawing tablet and sharing one mouse and keyboard between computers."""

import os
import unittest
from unittest import mock

from satellite import uinput
from satellite.plugins import shareinputdevices
from satellite.plugins.digitizer import MouseSession, Plugin as Digitizer, TabletSession
from satellite.plugins.mousepad import DesktopInput, _notches
from satellite.plugins.shareinputdevices import Plugin as Sharing, Receiver
from tests.fakes import LINK, FakeDaemon, packet

KEY, ABS = uinput.EV_KEY, uinput.EV_ABS


class FakeTablet:
    def __init__(self, *args):
        self.args, self.sent, self.closed = args, [], False

    def emit(self, events):
        self.sent.append(events)

    def close(self):
        self.closed = True


class VirtualTabletTests(unittest.TestCase):
    def test_device_is_set_up_like_kde_connects(self):
        calls, written = [], []
        tablet = uinput.VirtualTablet("Pixel 7 (Satellite)", 1080, 2000, 16, 16,
                                      opener=lambda path, flags: 7,
                                      ioctl=lambda fd, request, arg=0: calls.append((request, arg)),
                                      writer=lambda fd, data: written.append(data), closer=lambda fd: None)
        requests = [request for request, _arg in calls]
        self.assertEqual(requests[-1], uinput.UI_DEV_CREATE)
        self.assertIn((uinput.UI_SET_PROPBIT, uinput.INPUT_PROP_DIRECT), calls)
        self.assertIn((uinput.UI_SET_KEYBIT, uinput.BTN_TOOL_RUBBER), calls)
        x_axis = next(arg for request, arg in calls
                      if request == uinput.UI_ABS_SETUP and uinput.ABS_SETUP.unpack(arg)[0] == uinput.ABS_X)
        self.assertEqual(uinput.ABS_SETUP.unpack(x_axis), (uinput.ABS_X, 0, 0, 1079, 0, 0, 16))
        setup = uinput.SETUP.unpack(next(arg for request, arg in calls if request == uinput.UI_DEV_SETUP))
        self.assertEqual(setup[0], uinput.BUS_VIRTUAL)
        self.assertTrue(setup[4].startswith(b"Pixel 7 (Satellite)\0"))
        tablet.emit([(ABS, uinput.ABS_X, 10)])
        events = [uinput.EVENT.unpack_from(written[0], offset) for offset in (0, uinput.EVENT.size)]
        self.assertEqual([e[2:] for e in events], [(ABS, uinput.ABS_X, 10), (uinput.EV_SYN, 0, 0)])

    def test_a_refused_device_is_closed(self):
        closed = []

        def refuse(fd, request, arg=0):
            raise OSError(1, "Operation not permitted")
        with self.assertRaises(OSError):
            uinput.VirtualTablet("x", 10, 10, 1, 1, opener=lambda *a: 5, ioctl=refuse, closer=closed.append)
        self.assertEqual(closed, [5])


class TabletSessionTests(unittest.TestCase):
    def setUp(self):
        self.tablet = FakeTablet()
        self.session = TabletSession(self.tablet)

    def test_pen_stroke_with_pressure(self):
        self.session.event({"active": True, "touching": True, "tool": "Pen", "x": 5, "y": 6, "pressure": 0.5})
        self.assertEqual(self.tablet.sent[-1], [
            (KEY, uinput.BTN_TOUCH, 1), (KEY, uinput.BTN_TOOL_PEN, 1), (KEY, uinput.BTN_TOOL_RUBBER, 0),
            (ABS, uinput.ABS_X, 5), (ABS, uinput.ABS_Y, 6), (ABS, uinput.ABS_PRESSURE, 512)])

    def test_lifting_drops_pressure_and_leaving_resets(self):
        self.session.event({"touching": False, "pressure": 0.9})
        self.assertEqual(self.tablet.sent[-1], [(KEY, uinput.BTN_TOUCH, 0), (ABS, uinput.ABS_PRESSURE, 0)])
        self.session.event({"active": False})
        self.assertEqual(len(self.tablet.sent[-1]), 6)
        self.assertTrue(all(value == 0 for _k, _c, value in self.tablet.sent[-1]))

    def test_eraser(self):
        self.session.event({"tool": "Rubber"})
        self.assertEqual(self.tablet.sent[-1], [(KEY, uinput.BTN_TOOL_PEN, 0), (KEY, uinput.BTN_TOOL_RUBBER, 1)])


class MouseSessionTests(unittest.TestCase):
    def setUp(self):
        self.mouse = mock.Mock()
        self.session = MouseSession(1000, 500, (2000, 1000), self.mouse)

    def calls(self):
        return [(c[0], c[1]) for c in self.mouse.mock_calls]

    def test_pen_draws_with_the_button_held(self):
        self.session.event({"active": True, "touching": True, "x": 100, "y": 50, "pressure": 0.4})
        self.session.event({"x": 200, "y": 60, "pressure": 0.6})
        self.session.event({"active": True, "touching": False})
        self.assertEqual(self.calls(), [("point_at", (200, 100)), ("button", ("left", True)),
                                        ("point_at", (400, 120)), ("button", ("left", False))])
        self.assertTrue(self.mouse.point_at.call_args_list[0][1]["fresh"])

    def test_finger_draws_only_with_the_draw_button(self):
        # A finger alone just moves (pressure 0); with the pad's Draw button it draws (pressure 1).
        self.session.event({"active": True, "touching": False, "x": 10, "y": 10, "pressure": 0.0})
        self.session.event({"x": 20, "y": 10, "pressure": 1.0})
        self.session.event({"x": 30, "y": 10, "pressure": 0.0})
        self.assertEqual([c for c in self.calls() if c[0] == "button"],
                         [("button", ("left", True)), ("button", ("left", False))])

    def test_leaving_lets_go(self):
        self.session.event({"touching": True})
        self.session.event({"active": False})
        self.assertEqual(self.calls()[-1], ("button", ("left", False)))


class DigitizerPluginTests(unittest.TestCase):
    START = packet("kdeconnect.digitizer.session",
                   {"action": "start", "width": 1080, "height": 2000, "resolutionX": 16, "resolutionY": 16})

    def test_tablet_when_allowed(self):
        daemon = FakeDaemon()
        plugin = Digitizer(tablet_factory=FakeTablet, can_use_tablet=lambda: True, screen=lambda: (1920, 1080))
        plugin.handle_packet(daemon, LINK, self.START)
        tablet = plugin._sessions["dev1"].tablet
        self.assertEqual(tablet.args, ("Pixel 7 (Satellite)", 1080, 2000, 16, 16))
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.digitizer", {"x": 3}))
        self.assertEqual(tablet.sent, [[(ABS, uinput.ABS_X, 3)]])
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.digitizer.session", {"action": "end"}))
        self.assertTrue(tablet.closed)
        self.assertEqual(daemon.notifications, [])

    def test_mouse_without_permission_says_why_once(self):
        daemon = FakeDaemon()
        plugin = Digitizer(tablet_factory=FakeTablet, can_use_tablet=lambda: False, screen=lambda: (1920, 1080))
        plugin.handle_packet(daemon, LINK, self.START)
        plugin.handle_packet(daemon, LINK, self.START)
        self.assertIsInstance(plugin._sessions["dev1"], MouseSession)
        self.assertEqual(len(daemon.notifications), 1)
        self.assertIn("/dev/uinput", daemon.notifications[0]["body"])

    def test_mouse_when_chosen(self):
        daemon = FakeDaemon(settings={"digitizer": {"mode": "mouse"}})
        plugin = Digitizer(tablet_factory=FakeTablet, can_use_tablet=lambda: True, screen=lambda: (1920, 1080))
        plugin.handle_packet(daemon, LINK, self.START)
        self.assertIsInstance(plugin._sessions["dev1"], MouseSession)
        self.assertEqual(daemon.notifications, [])

    def test_bad_session_is_ignored(self):
        plugin = Digitizer(tablet_factory=FakeTablet, can_use_tablet=lambda: True)
        plugin.handle_packet(FakeDaemon(), LINK, packet("kdeconnect.digitizer.session",
                                                        {"action": "start", "width": "big"}))
        self.assertEqual(plugin._sessions, {})


class PointAtTests(unittest.TestCase):
    def test_portal_moves_from_the_corner(self):
        backend = mock.Mock()
        backend.move_to.side_effect = NotImplementedError
        mouse = DesktopInput(backend=backend)
        mouse.point_at(100, 50, fresh=True)
        mouse.point_at(130, 40)
        self.assertEqual([c[1] for c in backend.move.mock_calls], [(-100000, -100000), (100, 50), (30, -10)])

    def test_tools_that_can_jump(self):
        backend = mock.Mock()
        DesktopInput(backend=backend).point_at(7, 8)
        backend.move_to.assert_called_once_with(7, 8)


SCREENS = [(0, 0, 1920, 1080), (1920, 0, 1280, 1024)]


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.mouse = mock.Mock()
        self.receiver = Receiver(screens=lambda: SCREENS, mouse=self.mouse)

    def test_comes_in_on_the_facing_side_and_goes_back(self):
        # The other computer is to the left: its pointer left over its right edge.
        self.assertTrue(self.receiver.start("right", (0, 300)))
        self.mouse.point_at.assert_called_once_with(0, 300, fresh=True)
        self.assertIsNone(self.receiver.moved(400, 10))
        self.assertIsNone(self.receiver.moved(-399, 0))
        self.assertEqual(self.receiver.moved(-11, 0), (-10, 310))
        self.assertFalse(self.receiver.active)

    def test_picks_the_outermost_screen(self):
        self.receiver.start("left", (0, 100))  # comes in on the right: the second monitor
        self.mouse.point_at.assert_called_once_with(3199, 100, fresh=True)
        self.assertEqual(self.receiver.moved(5, 0), (4, 100))

    def test_other_sides_hold_the_pointer(self):
        self.receiver.start("right", (0, 0))
        self.assertIsNone(self.receiver.moved(0, -500))  # can't leave over the top
        self.assertEqual(self.receiver.position, [0, 0])


class FakeCapture:
    def __init__(self, on_start, on_input):
        self.on_start, self.on_input = on_start, on_input
        self.edges, self.released = None, []

    def set_edges(self, edges):
        self.edges = edges

    def release(self, device_id, delta):
        self.released.append((device_id, delta))


def peer_daemon(settings=None, disabled=()):
    return FakeDaemon(settings={"shareinputdevices": settings or {}}, disabled=disabled, devices={
        "dev1": {"id": "dev1", "connected": True,
                 "incomingCapabilities": ["kdeconnect.shareinputdevices.request"]}})


class SharingPluginTests(unittest.TestCase):
    def tearDown(self):
        shareinputdevices._receiving.clear()

    def test_sending_side(self):
        daemon = peer_daemon({"edge": "right"})
        plugin = Sharing(capture_factory=FakeCapture)
        plugin.on_connected(daemon, "dev1")
        capture = plugin._capture
        self.assertEqual(capture.edges, {"right": "dev1"})
        capture.on_start("dev1", "right", (0, 250))
        capture.on_input("dev1", {"dx": 3, "dy": 1})
        self.assertEqual([s[2:] for s in daemon.sent], [
            ("kdeconnect.shareinputdevices.request", {"exitEdge": 4, "deltax": 0, "deltay": 250}),
            ("kdeconnect.mousepad.request", {"dx": 3, "dy": 1})])
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.shareinputdevices",
                                                  {"releaseDeltax": -8, "releaseDeltay": 260}))
        self.assertEqual(capture.released, [("dev1", (-8, 260))])

    def test_off_by_default(self):
        plugin = Sharing(capture_factory=FakeCapture)
        plugin.on_connected(peer_daemon(), "dev1")
        self.assertIsNone(plugin._capture)

    def test_receiving_side(self):
        daemon = peer_daemon()
        receiver = Receiver(screens=lambda: SCREENS[:1], mouse=mock.Mock())
        plugin = Sharing(receiver_factory=lambda: receiver, capture_factory=FakeCapture)
        with mock.patch.object(DesktopInput, "available", True):
            plugin.handle_packet(daemon, LINK, packet("kdeconnect.shareinputdevices.request",
                                                      {"exitEdge": 2, "deltax": 0, "deltay": 500}))
        self.assertTrue(shareinputdevices.receiving("dev1"))
        # The other computer's left edge: the pointer comes in on the right and goes back right.
        self.assertFalse(plugin.handle_packet(daemon, LINK, packet("kdeconnect.mousepad.request",
                                                                   {"dx": -30, "dy": 0})))
        self.assertEqual(daemon.sent, [])
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.mousepad.request", {"dx": 35, "dy": 0}))
        self.assertEqual(daemon.sent[-1][2:], ("kdeconnect.shareinputdevices",
                                               {"releaseDeltax": 4, "releaseDeltay": 500}))
        self.assertFalse(shareinputdevices.receiving("dev1"))

    def test_handed_straight_back_without_mouse_and_keyboard(self):
        daemon = peer_daemon(disabled={"mousepad"})
        plugin = Sharing(capture_factory=FakeCapture)
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.shareinputdevices.request",
                                                  {"exitEdge": 2, "deltax": 0, "deltay": 5}))
        self.assertEqual(daemon.sent[-1][3], {"releaseDeltax": 0, "releaseDeltay": 0})

    def test_only_between_computers(self):
        self.assertFalse(Sharing.is_supported(["kdeconnect.mousepad.request"], ["kdeconnect.ping"]))
        self.assertTrue(Sharing.is_supported(["kdeconnect.shareinputdevices.request"], []))

    def test_scrolling_in_kde_steps(self):
        self.assertEqual([_notches(v, 15) for v in (15, -30, 4, 0)], [1, -2, 1, 0])
        self.assertEqual(_notches(3, 1), 3)


class CaptureGeometryTests(unittest.TestCase):
    def test_edges_and_return_point(self):
        os.environ.setdefault("GDK_BACKEND", "x11")
        from satellite.ui.input_capture import at_edge, return_point
        bounds = (0, 0, 3200, 1080)
        self.assertTrue(at_edge("right", 3199, 5, bounds))
        self.assertFalse(at_edge("right", 3000, 5, bounds))
        self.assertEqual(return_point("right", bounds, (-10, 300)), (3190, 300))
        self.assertEqual(return_point("left", bounds, (0, 2000)), (2, 1079))  # kept inside


if __name__ == "__main__":
    unittest.main()
