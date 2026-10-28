"""Use a paired phone as a presentation pointer.

The phone sends movement as fractions of the screen size, like KDE Connect.
Satellite draws a pointer in a click-through overlay (red dot or spotlight),
or moves the real mouse pointer. Slide changes arrive as key presses through
the Mouse and keyboard plugin, so Satellite says so when that one is off.
"""

import threading

from satellite.desktop_input import screen_size
from satellite.plugins._base import PacketStatePlugin
from satellite.plugins.mousepad import desktop_input, report_problems
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

SPEEDS = {"slow": 0.5, "normal": 1.0, "fast": 2.0}
FALLBACK_SCREEN = (1920, 1080)
MOUSEPAD = "mousepad"
SLIDES_NEED_MOUSEPAD = (_("Slides won't change: “Mouse and keyboard” is off for this device, and the "
                        "phone sends next and previous slide through it."))


class PointerPosition:
    """Where the pointer is, as fractions of the screen (KDE's algorithm)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.x = self.y = 0.5

    def move(self, dx, dy, ratio, speed=1.0):
        # dy is relative to the width, so it is scaled by the aspect ratio.
        self.x = min(1.0, max(0.0, self.x + dx * speed))
        self.y = min(1.0, max(0.0, self.y + dy * ratio * speed))
        return self.x, self.y


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class Plugin(PacketStatePlugin):
    plugin_id = "presenter"
    display_name = _("Presenter")
    description = _("Point at your slides from the paired device.")
    icon = "x-office-presentation-symbolic"
    incoming_capabilities = ("kdeconnect.presenter",)
    outgoing_capabilities = ("kdeconnect.presenter",)
    packet_types = incoming_capabilities
    # Edited in the plugin's own dialog with a live preview (configure below).
    settings_schema = (
        {"key": "style", "label": _("Pointer"), "type": "choice", "default": "dot",
         "options": (("dot", _("Dot")), ("image", _("Picture")), ("spotlight", _("Spotlight")),
                     ("cursor", _("Move the mouse pointer")))},
        {"key": "color", "label": _("Colour"), "type": "color", "default": "#e01b24"},
        {"key": "size", "label": _("Size"), "type": "choice", "default": "small",
         "options": (("small", _("Small")), ("medium", _("Medium")), ("large", _("Large")))},
        {"key": "opacity", "label": _("Opacity (%)"), "type": "int", "default": 80, "minimum": 10, "maximum": 100},
        {"key": "outline", "label": _("Outline"), "type": "choice", "default": "thin",
         "options": (("none", _("None")), ("thin", _("Thin")), ("thick", _("Thick")))},
        {"key": "outline_color", "label": _("Outline colour"), "type": "color", "default": "#ffffff"},
        {"key": "image", "label": _("Picture"), "type": "file", "default": ""},
        {"key": "trail", "label": _("Laser trail"), "type": "bool", "default": False},
        {"key": "spotlight_darkness", "label": _("Darkness (%)"), "type": "int", "default": 60,
         "minimum": 10, "maximum": 95},
        {"key": "spotlight_size", "label": _("Spotlight size"), "type": "choice", "default": "medium",
         "options": (("small", _("Small")), ("medium", _("Medium")), ("large", _("Large")))},
        {"key": "spotlight_ring", "label": _("Ring around the spotlight"), "type": "bool", "default": False},
        {"key": "hide_after", "label": _("Hide after"), "type": "choice", "default": "0.5",
         "options": (("0.5", _("Half a second")), ("2", _("2 seconds")), ("5", _("5 seconds")),
                     ("never", _("When the presentation ends")))},
        {"key": "speed", "label": _("Speed"), "type": "choice", "default": "normal",
         "options": (("slow", _("Slow")), ("normal", _("Normal")), ("fast", _("Fast")))},
    )

    def __init__(self, display=None, mover=None, limitation=None):
        self._display = display  # callable(x, y, settings) or None to stop
        self._mover = mover or desktop_input.move
        self._limitation = limitation  # tests replace the overlay check
        self._position = PointerPosition()
        self._overlay = None
        self._remainder = [0.0, 0.0]
        self._lock = threading.Lock()
        self._daemon = None
        self._warned = set()  # (device id, warning) already shown this session

    def on_connected(self, daemon, _device_id):
        report_problems(daemon)

    def _warn_once(self, device_id, key, *notification, **options):
        with self._lock:
            if (device_id, key) in self._warned:
                return
            self._warned.add((device_id, key))
        self._daemon.notify(*notification, **options)

    def handle_packet(self, daemon, link, packet):
        if not super().handle_packet(daemon, link, packet):
            return False
        self._daemon = daemon
        body = packet.get("body", {})
        settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
        if not daemon.plugins.is_enabled_for(link["id"], MOUSEPAD):
            self._warn_once(
                link["id"], MOUSEPAD, _("Slides won't change"), SLIDES_NEED_MOUSEPAD,
                icon=self.icon, timeout=15000,
                actions=[("enable", _("Turn on Mouse and keyboard"),
                          lambda: daemon.set_plugin_enabled(link["id"], MOUSEPAD, True))])
        if body.get("stop"):
            self._show(None, settings, link["id"])
            return True
        dx, dy = body.get("dx", 0), body.get("dy", 0)
        if not (_number(dx) and _number(dy)):
            return True
        speed = SPEEDS.get(settings.get("speed"), 1.0)
        width, height = self._screen_size()
        if settings.get("style") == "cursor":
            self._move_cursor(dx * width * speed, dy * width * speed)
            return True
        with self._lock:
            x, y = self._position.move(dx, dy, width / height, speed)
        self._show((x, y), settings, link["id"])
        return True

    def _check_overlay(self, device_id, settings):
        """Once a session: if the drawn pointer may not show, offer the mouse pointer."""
        limitation = self._limitation() if self._limitation else _overlay_limitation()
        if limitation is None:
            return
        from satellite.ui.presenter_overlay import LIMITATIONS

        def use_cursor():
            self._daemon.set_plugin_settings(device_id, self.plugin_id, dict(settings, style="cursor"))
        self._warn_once(
            device_id, limitation, _("The presenter pointer may not show"), LIMITATIONS[limitation],
            icon=self.icon, timeout=20000,
            actions=[("cursor", _("Move the mouse pointer instead"), use_cursor)])

    def _move_cursor(self, dx, dy):
        # Keep sub-pixel movement so slow, steady pointing still moves.
        with self._lock:
            self._remainder[0] += dx
            self._remainder[1] += dy
            step_x, step_y = int(self._remainder[0]), int(self._remainder[1])
            self._remainder[0] -= step_x
            self._remainder[1] -= step_y
        if step_x or step_y:
            try:
                self._mover(step_x, step_y)
            except Exception as error:
                print("Presenter could not move the pointer: {!r}".format(error), flush=True)

    def _show(self, position, settings, device_id):
        if position is None:
            with self._lock:
                self._position.reset()
        if self._display is not None:
            if position is not None:
                self._check_overlay(device_id, settings)
            self._display(position, settings)
            return
        try:
            from gi.repository import GLib
        except ImportError:
            return
        GLib.idle_add(self._show_overlay, position, settings, device_id)

    def _show_overlay(self, position, settings, device_id):
        """Runs on the GTK main loop."""
        if position is not None:
            self._check_overlay(device_id, settings)
        try:
            if self._overlay is None:
                from satellite.ui.presenter_overlay import PresenterOverlay
                self._overlay = PresenterOverlay()
            if position is None:
                self._overlay.stop()
            elif self._overlay.supported:
                self._overlay.point(position[0], position[1], settings)
        except (ImportError, ValueError, RuntimeError) as error:  # headless daemon
            print("Presenter overlay unavailable: {!r}".format(error), flush=True)
        return False

    def configure(self, ctx, settings):
        from satellite.ui.presenter_settings import Note, run_presenter_settings
        notes = []
        limitation = _overlay_limitation()
        if limitation is not None:
            from satellite.ui.presenter_overlay import LIMITATIONS
            notes.append(Note(LIMITATIONS[limitation], _("Move the mouse pointer instead"),
                              lambda dialog: dialog.choose("style", "cursor"), drawn_only=True))
        if not ctx.daemon.plugins.is_enabled_for(ctx.device_id, MOUSEPAD):
            notes.append(Note(SLIDES_NEED_MOUSEPAD, _("Turn it on"),
                              lambda _dialog: ctx.daemon.set_plugin_enabled(ctx.device_id, MOUSEPAD, True)))
        if not desktop_input.available:
            notes.append(Note(_("The mouse pointer can't be moved here. ") + desktop_input.status(),
                              cursor_only=True))
        return run_presenter_settings(ctx.parent, settings, self.settings_schema, notes)

    @staticmethod
    def _screen_size():
        return screen_size(FALLBACK_SCREEN)


def _overlay_limitation():
    try:
        from satellite.ui.presenter_overlay import overlay_limitation
        return overlay_limitation()
    except (ImportError, ValueError, RuntimeError):  # headless: nothing is drawn anyway
        return None
