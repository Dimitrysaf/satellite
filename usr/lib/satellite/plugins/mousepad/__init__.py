"""Mouse and keyboard event packet support."""

from .._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "mousepad"
    display_name = "Mouse and keyboard"
    description = "Receive touchpad and keyboard event packets."
    icon = "input-mouse-symbolic"
    incoming_capabilities = ("kdeconnect.mousepad.request",)
    outgoing_capabilities = (
        "kdeconnect.mousepad.echo",
        "kdeconnect.mousepad.keyboardstate",
    )
    packet_types = incoming_capabilities

    @classmethod
    def handle_packet(cls, daemon, link, packet):
        handled = super().handle_packet(daemon, link, packet)
        if not handled:
            return False
        daemon.inject_desktop_input(packet.get("body", {}))
        body = packet.get("body", {})
        if body.get("sendAck"):
            acknowledgement = {
                key: body[key]
                for key in ("key", "specialKey", "alt", "ctrl", "shift", "super")
                if key in body
            }
            acknowledgement["isAck"] = True
            daemon.send_plugin_packet(
                cls.plugin_id,
                link["id"],
                "kdeconnect.mousepad.echo",
                acknowledgement,
            )
        return True
