"""Use a paired phone as a touchpad and keyboard (X11, via xdotool)."""

import shutil
import subprocess

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

# KDE Connect's specialKey numbering (see kdeconnect-kde mousepad/x11remoteinput.cpp),
# mapped to xdotool key names. 0 is invalid and 17-20 are the bare modifiers.
SPECIAL_KEYS = {
    1: "BackSpace", 2: "Tab", 3: "Linefeed", 4: "Left", 5: "Up", 6: "Right",
    7: "Down", 8: "Page_Up", 9: "Page_Down", 10: "Home", 11: "End",
    12: "Return", 13: "Delete", 14: "Escape", 15: "Sys_Req",
    16: "Scroll_Lock", 17: "Control_L", 18: "Alt_L", 19: "Shift_L",
    20: "Super_L",
    **{number: "F{}".format(number - 20) for number in range(21, 33)},
}
MODIFIER_FIELDS = (("ctrl", "ctrl"), ("alt", "alt"), ("shift", "shift"), ("super", "super"))
BUTTON_SWAP = {"1": "3", "3": "1"}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class DesktopInput:
    """Turns KDE Connect pointer/keyboard bodies into xdotool calls."""

    def __init__(self, xdotool=None, runner=subprocess.run):
        self._xdotool = xdotool if xdotool is not None else shutil.which("xdotool")
        self._runner = runner

    @property
    def available(self):
        return bool(self._xdotool)

    def _run(self, *arguments):
        self._runner(
            [self._xdotool, *arguments], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )

    def apply(self, body, left_handed=False):
        if not self._xdotool:
            return False

        def button(number):
            return BUTTON_SWAP.get(number, number) if left_handed else number

        try:
            dx, dy = body.get("dx", 0), body.get("dy", 0)
            if _number(dx) and _number(dy) and (dx or dy) and not body.get("scroll"):
                self._run("mousemove_relative", "--sync", str(round(dx)), str(round(dy)))

            if body.get("scroll") and _number(dy):
                amount = max(-20, min(20, round(dy)))
                if amount:
                    self._run(
                        "click", "--repeat", str(abs(amount)), "--delay", "20",
                        "4" if amount > 0 else "5",
                    )

            for field, arguments in (
                ("singleclick", ("click", button("1"))),
                ("doubleclick", ("click", "--repeat", "2", "--delay", "100", button("1"))),
                ("middleclick", ("click", "2")),
                ("rightclick", ("click", button("3"))),
                ("singlehold", ("mousedown", button("1"))),
                ("singlerelease", ("mouseup", button("1"))),
            ):
                if body.get(field):
                    self._run(*arguments)

            modifiers = [name for field, name in MODIFIER_FIELDS if body.get(field)]
            key = body.get("key")
            if isinstance(key, str) and key and len(key) <= 256:
                if modifiers and len(key) == 1 and key.isalnum():
                    # Shortcuts such as Ctrl+C must be sent as key chords.
                    self._run("key", "+".join(modifiers + [key]))
                else:
                    self._run("type", "--clearmodifiers", "--delay", "0", key)
            special = body.get("specialKey")
            if _number(special) and special in SPECIAL_KEYS:
                self._run("key", "+".join(modifiers + [SPECIAL_KEYS[special]]))
            return True
        except (OSError, subprocess.SubprocessError):
            return False


desktop_input = DesktopInput()


class Plugin(PacketStatePlugin):
    plugin_id = "mousepad"
    display_name = "Mouse and keyboard"
    description = "Use the paired device as a touchpad and keyboard."
    icon = "input-mouse-symbolic"
    incoming_capabilities = ("kdeconnect.mousepad.request",)
    outgoing_capabilities = (
        "kdeconnect.mousepad.echo",
        "kdeconnect.mousepad.keyboardstate",
    )
    packet_types = incoming_capabilities
    settings_schema = (
        {"key": "left_handed", "label": "Swap left and right buttons", "type": "bool", "default": False},
    )

    def on_connected(self, daemon, device_id):
        daemon.send_plugin_packet(
            self.plugin_id, device_id, "kdeconnect.mousepad.keyboardstate",
            {"state": desktop_input.available},
        )

    def handle_packet(self, daemon, link, packet):
        if not super().handle_packet(daemon, link, packet):
            return False
        body = packet.get("body", {})
        settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
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
