"""Use a paired phone as a presentation pointer."""

from satellite.plugins._base import PacketStatePlugin
from satellite.plugins.mousepad import desktop_input

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "presenter"
    display_name = "Presenter"
    description = "Move the pointer from the paired device during a presentation."
    icon = "media-playback-start-symbolic"
    incoming_capabilities = ("kdeconnect.presenter",)
    outgoing_capabilities = ("kdeconnect.presenter",)
    packet_types = incoming_capabilities

    def handle_packet(self, daemon, link, packet):
        if not super().handle_packet(daemon, link, packet):
            return False
        body = packet.get("body", {})
        if not body.get("stop"):
            desktop_input.apply({"dx": body.get("dx", 0), "dy": body.get("dy", 0)})
        return True
