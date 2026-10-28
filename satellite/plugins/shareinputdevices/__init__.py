"""Share one mouse and keyboard between this computer and another one.

KDE Connect's protocol, so it also works with KDE Plasma computers. Push the
pointer against the chosen edge of the screen and it carries on on the other
computer, with the keyboard; bring it back over the same edge to return.

* Sending (this computer's mouse and keyboard): the edge is set per device in
  this plugin's settings. Satellite then grabs the pointer and keyboard and
  sends them as ordinary touchpad packets. This needs an X11 session
  (Cinnamon's default); Ctrl+Alt+Escape always takes them back.
* Receiving (the other computer's): works on X11 and Wayland, through the
  Mouse and keyboard plugin, which moves the pointer and types here.
"""

import threading

from satellite.plugins._base import PacketStatePlugin
from satellite.plugins.mousepad import desktop_input, report_problems
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

REQUEST = "kdeconnect.shareinputdevices.request"
REPLY = "kdeconnect.shareinputdevices"
MOUSEPAD = "kdeconnect.mousepad.request"
QT_EDGES = {"top": 0x1, "left": 0x2, "right": 0x4, "bottom": 0x8}  # Qt::Edge, as KDE sends it
EDGE_NAMES = {value: name for name, value in QT_EDGES.items()}
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}
SCROLL_STEP = 15  # KDE sends 15 per wheel notch (degrees)

_receiving = set()  # device ids whose mouse and keyboard are in use here


def receiving(device_id):
    """Whether this device is using its mouse and keyboard here (the Mouse and
    keyboard plugin then scrolls in KDE's 15-per-notch steps)."""
    return device_id in _receiving


def all_screens():
    """Monitor rectangles (x, y, width, height) in device pixels."""
    try:
        import gi
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        screens = []
        for index in range(display.get_n_monitors()):
            monitor = display.get_monitor(index)
            area, scale = monitor.get_geometry(), monitor.get_scale_factor()
            screens.append((area.x * scale, area.y * scale, area.width * scale, area.height * scale))
        return screens
    except (ImportError, ValueError, AttributeError, TypeError):
        return []


class Receiver:
    """Where the other computer's pointer is on this one, and when it leaves.

    Mirrors KDE Connect's shareinputdevicesremote: the pointer comes in on
    the side facing the other computer, and going back over that side hands
    it back with how far past the edge it went.
    """

    def __init__(self, screens=all_screens, mouse=desktop_input):
        self._screens = screens
        self._mouse = mouse
        self.screen = None
        self.enter = None
        self.position = None
        self.bounds = None

    @property
    def active(self):
        return self.screen is not None

    def start(self, exit_edge, delta):
        screens = self._screens()
        if not screens or exit_edge not in OPPOSITE:
            return False
        self.enter = OPPOSITE[exit_edge]
        # The screen furthest towards the other computer, like KDE picks it.
        pick = {"right": lambda s: -(s[0] + s[2]), "left": lambda s: s[0],
                "bottom": lambda s: -(s[1] + s[3]), "top": lambda s: s[1]}[self.enter]
        x, y, width, height = self.screen = min(screens, key=pick)
        corner = {"right": (x + width, y), "left": (x, y), "bottom": (x, y + height), "top": (x, y)}[self.enter]
        self.position = [corner[0] + delta[0], corner[1] + delta[1]]
        self.bounds = (min(s[0] for s in screens), min(s[1] for s in screens),
                       max(s[0] + s[2] for s in screens), max(s[1] + s[3] for s in screens))
        self._keep_inside()
        # On the screen's last pixel when coming in from the right or the bottom.
        self.position[0] = min(self.position[0], x + width - 1)
        self.position[1] = min(self.position[1], y + height - 1)
        self._mouse.point_at(round(self.position[0]), round(self.position[1]), fresh=True)
        return True

    def _keep_inside(self):
        left, top, right, bottom = self.bounds
        # Not on the entering side: going past it is how the pointer leaves.
        if self.enter != "left":
            self.position[0] = max(left, self.position[0])
        if self.enter != "right":
            self.position[0] = min(right - 1, self.position[0])
        if self.enter != "top":
            self.position[1] = max(top, self.position[1])
        if self.enter != "bottom":
            self.position[1] = min(bottom - 1, self.position[1])

    def moved(self, dx, dy):
        """Follow a movement; returns the release delta once the pointer leaves, else None."""
        if not self.active:
            return None
        self.position[0] += dx
        self.position[1] += dy
        self._keep_inside()
        x, y, width, height = self.screen
        px, py = self.position
        leaving = {"left": px < x, "right": px > x + width, "top": py < y, "bottom": py > y + height}[self.enter]
        if not leaving:
            return None
        corner = {"left": (x, y), "right": (x + width, y), "top": (x, y), "bottom": (x, y + height)}[self.enter]
        self.screen = None
        return px - corner[0], py - corner[1]

    def stop(self):
        self.screen = None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class Plugin(PacketStatePlugin):
    plugin_id = "shareinputdevices"
    display_name = _("Share mouse and keyboard")
    description = _("Use one mouse and keyboard on this computer and another one, across the screen edge.")
    icon = "computer-symbolic"
    incoming_capabilities = (REPLY, REQUEST)
    outgoing_capabilities = (REQUEST, MOUSEPAD, REPLY)
    packet_types = (REPLY, REQUEST, MOUSEPAD)
    settings_schema = (
        {"key": "edge", "label": _("Where the other computer is"), "type": "choice", "default": "off",
         "options": (("off", _("Don't send this computer's mouse and keyboard")),
                     ("left", _("Left of this screen")), ("right", _("Right of this screen")),
                     ("top", _("Above this screen")), ("bottom", _("Below this screen")))},
    )

    def __init__(self, receiver_factory=Receiver, capture_factory=None):
        self._receiver_factory = receiver_factory
        self._capture_factory = capture_factory  # tests replace the X11 capture
        self._receivers = {}
        self._capture = None
        self._daemon = None
        self._lock = threading.Lock()

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        # Only computers running KDE Connect or Satellite take part; phones don't.
        return REQUEST in remote_incoming or REQUEST in remote_outgoing

    @staticmethod
    def settings_notes(_daemon, _device_id):
        from satellite.desktop_input import is_wayland
        notes = [_("Bring the pointer back over the same edge to return. Ctrl+Alt+Escape always "
                 "takes the mouse and keyboard back.")]
        if is_wayland():
            notes.insert(0, _("On Wayland this computer can only receive the other one's mouse and "
                            "keyboard: sending them needs an X11 session."))
        return notes

    # -- connection life ----------------------------------------------------------

    def on_connected(self, daemon, _device_id):
        self._daemon = daemon
        report_problems(daemon)
        self._update_edges(daemon)

    def settings_changed(self, daemon, _device_id):
        self._daemon = daemon
        self._update_edges(daemon)

    def on_disconnected(self, daemon, device_id):
        with self._lock:
            receiver = self._receivers.pop(device_id, None)
        if receiver is not None:
            receiver.stop()
        _receiving.discard(device_id)
        self._on_main(lambda: self._capture and self._capture.release(device_id, None))
        self._update_edges(daemon, gone=device_id)

    def _update_edges(self, daemon, gone=None):
        """Tell the capture which edge leads to which connected device."""
        edges = {}
        for device in daemon.devices():
            device_id = device["id"]
            if device_id == gone or not device.get("connected"):
                continue
            if not daemon.plugins.is_enabled_for(device_id, self.plugin_id):
                continue
            if REQUEST not in device.get("incomingCapabilities", []):
                continue
            edge = daemon.plugins.get_settings(device_id, self.plugin_id).get("edge", "off")
            if edge in QT_EDGES:
                edges.setdefault(edge, device_id)
        self._on_main(lambda: self._set_edges(edges))

    def _set_edges(self, edges):
        if self._capture is None:
            if not edges:
                return
            factory = self._capture_factory
            if factory is None:
                try:
                    from satellite.ui.input_capture import InputCapture
                except (ImportError, ValueError, RuntimeError) as error:  # no display: receive only
                    print("Can't send this computer's mouse and keyboard: {!r}".format(error), flush=True)
                    return
                factory = InputCapture
            self._capture = factory(self._started, self._send_input)
        self._capture.set_edges(edges)

    def _on_main(self, callback):
        if self._capture_factory is not None:  # tests: no main loop
            callback()
            return
        try:
            from gi.repository import GLib
        except ImportError:
            return
        GLib.idle_add(lambda: callback() and False)

    # -- sending this computer's mouse and keyboard -------------------------------

    def _started(self, device_id, edge, delta):
        self._daemon.send_plugin_packet(self.plugin_id, device_id, REQUEST, {
            "exitEdge": QT_EDGES[edge], "deltax": delta[0], "deltay": delta[1]})

    def _send_input(self, device_id, body):
        self._daemon.send_plugin_packet(self.plugin_id, device_id, MOUSEPAD, body)

    # -- packets --------------------------------------------------------------------

    def handle_packet(self, daemon, link, packet):
        packet_type, body = packet.get("type"), packet.get("body", {})
        device_id = link["id"]
        if packet_type == REPLY:
            # The other computer is done with our mouse and keyboard.
            delta = (body.get("releaseDeltax"), body.get("releaseDeltay"))
            delta = delta if all(_number(v) for v in delta) else (0, 0)
            self._on_main(lambda: self._capture and self._capture.release(device_id, delta))
            return True
        if packet_type == REQUEST:
            self._daemon = daemon
            self._receive(daemon, device_id, body)
            return True
        if packet_type == MOUSEPAD and not body.get("scroll"):
            # The Mouse and keyboard plugin moves the pointer; this only follows it.
            with self._lock:
                receiver = self._receivers.get(device_id)
            dx, dy = body.get("dx"), body.get("dy")
            if receiver is not None and _number(dx) and _number(dy):
                delta = receiver.moved(dx, dy)
                if delta is not None:
                    _receiving.discard(device_id)
                    daemon.send_plugin_packet(self.plugin_id, device_id, REPLY,
                                              {"releaseDeltax": delta[0], "releaseDeltay": delta[1]})
        return False  # the Mouse and keyboard plugin handles it

    def _receive(self, daemon, device_id, body):
        edge = EDGE_NAMES.get(body.get("exitEdge"))
        delta = (body.get("deltax", 0), body.get("deltay", 0))
        with self._lock:
            receiver = self._receivers.get(device_id) or self._receiver_factory()
            self._receivers[device_id] = receiver
        usable = (edge is not None and all(_number(v) for v in delta)
                  and daemon.plugins.is_enabled_for(device_id, "mousepad") and desktop_input.available)
        if usable and receiver.start(edge, delta):
            _receiving.add(device_id)
            return
        # Can't take it: hand the mouse and keyboard straight back.
        daemon.send_plugin_packet(self.plugin_id, device_id, REPLY, {"releaseDeltax": 0, "releaseDeltay": 0})
