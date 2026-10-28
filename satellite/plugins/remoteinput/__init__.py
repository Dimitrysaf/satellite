"""Control the paired device's pointer and keyboard from this computer.

Covers KDE Connect's remotecontrol and remotekeyboard plugins: both send
kdeconnect.mousepad.request packets to the device. Typing needs the
"KDE Connect Remote Keyboard" input method switched on on the phone.
"""

from satellite.plugins._base import PacketStatePlugin, PluginAction
from satellite.plugins.mousepad import SPECIAL_KEYS
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

SPECIAL_NUMBERS = {name: number for number, name in SPECIAL_KEYS.items() if number not in range(17, 21)}
SPECIAL_NUMBERS.update({"KP_Enter": 12, "ISO_Left_Tab": 2, "KP_Delete": 13})
SPEEDS = {"slow": 0.6, "normal": 1.0, "fast": 1.8}


def key_body(key_name, char, ctrl=False, alt=False, shift=False, super_=False):
    """The mousepad.request body for one key press, or None to ignore it.

    ``key_name`` is the GDK key name ("Return", "a", ...); ``char`` is the
    text the key types, or "" for keys that type nothing.
    """
    special = SPECIAL_NUMBERS.get(key_name)
    if key_name == "ISO_Left_Tab":
        shift = True
    if special is not None:
        body = {"specialKey": special}
    elif char and char.isprintable():
        body = {"key": char}
        # Shift is already part of the character unless it forms a shortcut.
        shift = shift and (ctrl or alt or super_)
    else:
        return None  # bare modifiers and keys the protocol can't express
    body.update({"ctrl": ctrl, "alt": alt, "shift": shift, "super": super_, "sendAck": False})
    return body


class Plugin(PacketStatePlugin):
    plugin_id = "remoteinput"
    display_name = _("Remote input")
    description = _("Use this computer's mouse and keyboard on the paired device.")
    icon = "input-keyboard-symbolic"
    incoming_capabilities = ("kdeconnect.mousepad.echo", "kdeconnect.mousepad.keyboardstate")
    outgoing_capabilities = ("kdeconnect.mousepad.request",)
    packet_types = incoming_capabilities
    settings_schema = (
        {"key": "speed", "label": _("Pointer speed"), "type": "choice", "default": "normal",
         "options": (("slow", _("Slow")), ("normal", _("Normal")), ("fast", _("Fast")))},
    )

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.mousepad.request" in remote_incoming

    def handle_packet(self, daemon, link, packet):
        if packet.get("type") != "kdeconnect.mousepad.keyboardstate":
            return packet.get("type") == "kdeconnect.mousepad.echo"
        return super().handle_packet(daemon, link, packet)

    @staticmethod
    def keyboard_ready(device):
        data = device.get("plugin_data", {}).get("remoteinput", {})
        return bool(data.get("body", {}).get("state"))

    def actions(self, device):
        def run(ctx):
            from satellite.ui.remote_input import RemoteInputWindow
            settings = ctx.daemon.plugin_settings(ctx.device_id, self.plugin_id)
            RemoteInputWindow(
                ctx.parent, ctx.device["name"],
                lambda body: ctx.daemon.send_plugin_packet(
                    self.plugin_id, ctx.device_id, "kdeconnect.mousepad.request", body),
                SPEEDS.get(settings.get("speed"), 1.0),
                self.keyboard_ready(ctx.device),
            ).present()

        return [PluginAction(
            "remote-input", _("Control device…"), "input-mouse-symbolic", run,
            _("Use this computer's mouse and keyboard on the device"), opens_window=True,
        )]
