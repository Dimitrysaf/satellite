"""Use the device's screen as a drawing tablet for this computer.

KDE Connect's Android app has a "Use as drawing tablet" pad. With a stylus,
strokes come with the pen's pressure (and its eraser end); without one, hold
the pad's "Draw using finger" button and draw with another finger at full
pressure.

Satellite creates a real virtual pen tablet when it may (see
``satellite.uinput``), so drawing apps such as Krita, GIMP or Xournal++ get
pressure. Otherwise, or if chosen, strokes move the mouse with its button
held: any app can be drawn in, just without pressure.
"""

import threading

from satellite import uinput
from satellite.desktop_input import screen_size
from satellite.plugins._base import PacketStatePlugin
from satellite.plugins.mousepad import desktop_input, report_problems
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

PEN, RUBBER = "Pen", "Rubber"
NEEDS_PERMISSION = (_("Drawing works as a mouse, without pen pressure: Satellite may not create a virtual "
                    "drawing tablet yet. Log out and back in after installing Satellite (its udev rule "
                    "gives you access to /dev/uinput)."))


class TabletSession:
    """Forwards the pad's events to a virtual pen tablet, as KDE Connect does."""

    def __init__(self, tablet):
        self.tablet = tablet

    def event(self, body):
        events = []
        if body.get("active") is False:
            # The pen left the pad: nothing is being tracked any more.
            events = [(uinput.EV_KEY, uinput.BTN_TOOL_PEN, 0), (uinput.EV_KEY, uinput.BTN_TOOL_RUBBER, 0),
                      (uinput.EV_KEY, uinput.BTN_TOUCH, 0), (uinput.EV_ABS, uinput.ABS_PRESSURE, 0),
                      (uinput.EV_ABS, uinput.ABS_X, 0), (uinput.EV_ABS, uinput.ABS_Y, 0)]
        else:
            lifted = False
            if isinstance(body.get("touching"), bool):
                events.append((uinput.EV_KEY, uinput.BTN_TOUCH, int(body["touching"])))
                if not body["touching"]:
                    events.append((uinput.EV_ABS, uinput.ABS_PRESSURE, 0))
                    lifted = True
            if body.get("tool") in (PEN, RUBBER):
                events += [(uinput.EV_KEY, uinput.BTN_TOOL_PEN, int(body["tool"] == PEN)),
                           (uinput.EV_KEY, uinput.BTN_TOOL_RUBBER, int(body["tool"] == RUBBER))]
            for field, code in (("x", uinput.ABS_X), ("y", uinput.ABS_Y)):
                if _number(body.get(field)):
                    events.append((uinput.EV_ABS, code, int(body[field])))
            if not lifted and _number(body.get("pressure")):
                pressure = max(0.0, min(1.0, float(body["pressure"])))
                events.append((uinput.EV_ABS, uinput.ABS_PRESSURE, round(pressure * uinput.PRESSURE_MAX)))
        if events:
            self.tablet.emit(events)

    def close(self):
        self.tablet.close()


class MouseSession:
    """Draws by moving the mouse with its button held: works everywhere, no pressure."""

    def __init__(self, width, height, screen, mouse=desktop_input):
        self.width, self.height = width, height
        self.screen = screen
        self.mouse = mouse
        self.down = False
        self.fresh = True

    def event(self, body):
        if body.get("active") is False:
            self._set_down(False)
            self.fresh = True
            return
        if _number(body.get("x")) and _number(body.get("y")):
            x = round(body["x"] * self.screen[0] / self.width)
            y = round(body["y"] * self.screen[1] / self.height)
            self.mouse.point_at(x, y, fresh=self.fresh)
            self.fresh = False
        touching, pressure = body.get("touching"), body.get("pressure")
        if touching is True:
            self._set_down(True)
        elif touching is False:
            # A finger drawing with the pad's Draw button touches with full pressure.
            self._set_down(_number(pressure) and pressure > 0)
        elif _number(pressure):
            self._set_down(pressure > 0)

    def _set_down(self, down):
        if down != self.down:
            self.down = down
            self.mouse.button("left", down)

    def close(self):
        self._set_down(False)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class Plugin(PacketStatePlugin):
    plugin_id = "digitizer"
    display_name = _("Drawing tablet")
    description = _("Draw on this computer on the device's screen, with a pen or a finger.")
    icon = "input-tablet-symbolic"
    incoming_capabilities = ("kdeconnect.digitizer.session", "kdeconnect.digitizer")
    outgoing_capabilities = ()
    packet_types = incoming_capabilities
    settings_schema = (
        {"key": "mode", "label": _("Draw as"), "type": "choice", "default": "auto",
         "options": (("auto", _("A drawing tablet with pen pressure, if possible")),
                     ("mouse", _("The mouse (no pressure, works in every app)")))},
    )

    def __init__(self, tablet_factory=uinput.VirtualTablet, can_use_tablet=uinput.available,
                 screen=screen_size):
        self._tablet_factory = tablet_factory
        self._can_use_tablet = can_use_tablet
        self._screen = screen
        self._sessions = {}
        self._warned = False
        self._lock = threading.Lock()

    @staticmethod
    def settings_notes(_daemon, _device_id):
        if uinput.available():
            return [_("Drawing apps get pen pressure and the eraser.")]
        return [NEEDS_PERMISSION]

    def on_connected(self, daemon, _device_id):
        report_problems(daemon)

    def on_disconnected(self, _daemon, device_id):
        self._end(device_id)

    def handle_packet(self, daemon, link, packet):
        packet_type = packet.get("type")
        if packet_type not in self.packet_types:
            return False
        body = packet.get("body", {})
        if packet_type == "kdeconnect.digitizer.session":
            if body.get("action") == "start":
                self._start(daemon, link, body)
            elif body.get("action") == "end":
                self._end(link["id"])
            return True
        with self._lock:
            session = self._sessions.get(link["id"])
        if session is not None:
            try:
                session.event(body)
            except OSError as error:
                print("Drawing tablet failed: {!r}".format(error), flush=True)
                self._end(link["id"])
        return True

    def _start(self, daemon, link, body):
        size = [body.get(key) for key in ("width", "height", "resolutionX", "resolutionY")]
        if not all(_number(value) and value > 0 for value in size):
            return
        width, height, resolution_x, resolution_y = (int(value) for value in size)
        self._end(link["id"])
        settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
        session = None
        if settings.get("mode", "auto") == "auto" and self._can_use_tablet():
            try:
                session = TabletSession(self._tablet_factory(
                    "{} (Satellite)".format(link["name"]), width, height, resolution_x, resolution_y))
            except OSError as error:
                print("Could not create the virtual drawing tablet: {!r}".format(error), flush=True)
        if session is None:
            if settings.get("mode", "auto") == "auto" and not self._warned:
                self._warned = True
                daemon.notify(_("Drawing without pen pressure"), NEEDS_PERMISSION,
                              icon=self.icon, timeout=15000)
            session = MouseSession(width, height, self._screen())
        with self._lock:
            self._sessions[link["id"]] = session

    def _end(self, device_id):
        with self._lock:
            session = self._sessions.pop(device_id, None)
        if session is not None:
            try:
                session.close()
            except OSError:
                pass
