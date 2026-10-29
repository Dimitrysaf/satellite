"""Use a paired phone as a touchpad and keyboard (X11 and Wayland)."""

import os
import subprocess
import threading

from satellite.desktop_input import XdotoolBackend, pick_backend, problem_text, status_text
from satellite.plugins._base import PacketStatePlugin
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

# KDE Connect's specialKey numbering (see kdeconnect-kde mousepad/x11remoteinput.cpp),
# mapped to xdotool-style key names. 0 is invalid and 17-20 are the bare modifiers.
SPECIAL_KEYS = {
    1: "BackSpace", 2: "Tab", 3: "Linefeed", 4: "Left", 5: "Up", 6: "Right",
    7: "Down", 8: "Page_Up", 9: "Page_Down", 10: "Home", 11: "End",
    12: "Return", 13: "Delete", 14: "Escape", 15: "Sys_Req",
    16: "Scroll_Lock", 17: "Control_L", 18: "Alt_L", 19: "Shift_L",
    20: "Super_L",
    **{number: "F{}".format(number - 20) for number in range(21, 33)},
}
MODIFIER_FIELDS = (("ctrl", "ctrl"), ("alt", "alt"), ("shift", "shift"), ("super", "super"))
BUTTON_SWAP = {"left": "right", "right": "left"}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _notches(value, step):
    """Wheel notches for a scroll amount; any movement makes at least one."""
    if step == 1 or not value:
        return round(value)
    notches = round(value / step)
    return notches or (1 if value > 0 else -1)


def _token_path():
    # Same folder as the daemon's config; remembers the Wayland input permission.
    return os.path.join(os.path.expanduser("~"), ".config", "satellite", "remote-desktop-token")


class DesktopInput:
    """Turns KDE Connect pointer/keyboard bodies into desktop input.

    The backend (xdotool, the RemoteDesktop portal or ydotool) is chosen on
    first use; tests pass ``xdotool`` and ``runner`` to force xdotool.

    When input can't be injected, ``on_problem(text)`` is told once per kind
    of problem, so the user learns what to fix instead of nothing happening.
    """

    def __init__(self, xdotool=None, runner=subprocess.run, backend=None):
        if backend is None and xdotool is not None:
            backend = XdotoolBackend(xdotool, runner) if xdotool else False
        self._backend = backend  # None: not chosen yet; False: none available
        self._lock = threading.Lock()
        self.on_problem = None
        self._reported = set()
        self._at = None  # where point_at() last put the pointer, for relative-only backends

    @property
    def backend(self):
        with self._lock:
            if self._backend is None:
                self._backend = pick_backend(_token_path()) or False
            return self._backend or None

    @property
    def available(self):
        return self.backend is not None

    def status(self):
        return status_text(self.backend)

    def _problem(self, backend, error=None):
        text = problem_text(backend, error)
        with self._lock:
            if text in self._reported:
                return
            self._reported.add(text)
        if self.on_problem is not None:
            self.on_problem(text)

    def move(self, dx, dy):
        return self.apply({"dx": dx, "dy": dy})

    def _run(self, action):
        backend = self.backend
        if backend is None:
            self._problem(None)
            return False
        try:
            action(backend)
            return True
        except Exception as error:
            print("Desktop input failed ({}): {!r}".format(backend.name, error), flush=True)
            self._problem(backend, error)
            return False

    def point_at(self, x, y, fresh=False):
        """Put the pointer at (x, y) screen pixels.

        The Wayland portal can only move relatively, so it starts from the
        top-left corner (pushed against it) whenever ``fresh`` is set.
        """
        def place(backend):
            try:
                backend.move_to(x, y)
            except NotImplementedError:
                if fresh or self._at is None:
                    backend.move(-100000, -100000)
                    self._at = (0, 0)
                backend.move(x - self._at[0], y - self._at[1])
            self._at = (x, y)
        return self._run(place)

    def button(self, name, pressed):
        return self._run(lambda backend: (backend.press if pressed else backend.release)(name))

    def apply(self, body, left_handed=False, scroll_step=1):
        """``scroll_step`` is how much of the body's scroll makes one wheel notch."""
        backend = self.backend
        if backend is None:
            self._problem(None)
            return False

        def button(name):
            return BUTTON_SWAP.get(name, name) if left_handed else name

        try:
            dx, dy = body.get("dx", 0), body.get("dy", 0)
            if _number(dx) and _number(dy) and (dx or dy) and not body.get("scroll"):
                backend.move(dx, dy)

            if body.get("scroll"):
                for value, horizontal in ((dy, False), (dx, True)):
                    amount = max(-20, min(20, _notches(value, scroll_step))) if _number(value) else 0
                    if amount:
                        backend.scroll(amount, horizontal=horizontal)

            if body.get("singleclick"):
                backend.click(button("left"))
            if body.get("doubleclick"):
                backend.click(button("left"), count=2)
            if body.get("middleclick"):
                backend.click("middle")
            if body.get("rightclick"):
                backend.click(button("right"))
            if body.get("singlehold"):
                backend.press(button("left"))
            if body.get("singlerelease"):
                backend.release(button("left"))

            modifiers = [name for field, name in MODIFIER_FIELDS if body.get(field)]
            key = body.get("key")
            if isinstance(key, str) and key and len(key) <= 256:
                if modifiers and len(key) == 1 and key.isalnum():
                    # Shortcuts such as Ctrl+C must be sent as key chords.
                    backend.key(modifiers + [key.lower()])
                else:
                    backend.type_text(key)
            special = body.get("specialKey")
            if _number(special) and special in SPECIAL_KEYS:
                backend.key(modifiers + [SPECIAL_KEYS[special]])
            return True
        except Exception as error:  # a failed or refused backend must not drop the link
            print("Desktop input failed ({}): {!r}".format(backend.name, error), flush=True)
            self._problem(backend, error)
            return False


desktop_input = DesktopInput()


def report_problems(daemon):
    """Show input problems as a notification (once each)."""
    def notify(text):
        daemon.notify(_("The phone can't control this computer"), text,
                      icon="input-mouse-symbolic", timeout=15000)
    desktop_input.on_problem = notify


class Plugin(PacketStatePlugin):
    plugin_id = "mousepad"
    display_name = _("Mouse and keyboard")
    description = _("Use the paired device as a touchpad and keyboard.")
    icon = "input-mouse-symbolic"
    incoming_capabilities = ("kdeconnect.mousepad.request",)
    outgoing_capabilities = (
        "kdeconnect.mousepad.echo",
        "kdeconnect.mousepad.keyboardstate",
    )
    packet_types = incoming_capabilities
    remember_packets = False  # pointer moves arrive dozens of times a second
    settings_schema = (
        {"key": "left_handed", "label": _("Swap left and right buttons"), "type": "bool", "default": False},
    )

    def on_connected(self, daemon, device_id):
        report_problems(daemon)
        daemon.send_plugin_packet(
            self.plugin_id, device_id, "kdeconnect.mousepad.keyboardstate",
            {"state": desktop_input.available},
        )

    def handle_packet(self, daemon, link, packet):
        if not super().handle_packet(daemon, link, packet):
            return False
        body = packet.get("body", {})
        settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
        from satellite.plugins.shareinputdevices import SCROLL_STEP, receiving
        if receiving(link["id"]):
            # Another computer's own mouse: already set up the way its owner likes.
            desktop_input.apply(body, scroll_step=SCROLL_STEP)
        else:
            desktop_input.apply(body, bool(settings.get("left_handed", False)))
        if body.get("sendAck"):
            acknowledgement = {
                key: body[key]
                for key in ("key", "specialKey", "alt", "ctrl", "shift", "super")
                if key in body
            }
            acknowledgement["isAck"] = True
            daemon.send_plugin_packet(
                self.plugin_id, link["id"], "kdeconnect.mousepad.echo", acknowledgement
            )
        return True

    @staticmethod
    def settings_notes(_daemon, _device_id):
        """Shown above the settings: what moves the pointer on this desktop."""
        return [desktop_input.status()]
