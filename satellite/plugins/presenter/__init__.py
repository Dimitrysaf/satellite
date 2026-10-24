"""Presentation event packet support."""

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "presenter"
    display_name = "Presenter"
    description = "Receive presentation remote events."
    icon = "media-playback-start-symbolic"
    incoming_capabilities = ("kdeconnect.presenter",)
    outgoing_capabilities = ("kdeconnect.presenter",)
    packet_types = incoming_capabilities

    @classmethod
    def handle_packet(cls, daemon, link, packet):
        handled = super().handle_packet(daemon, link, packet)
        if handled:
            daemon.inject_desktop_input(packet.get("body", {}))
        return handled
