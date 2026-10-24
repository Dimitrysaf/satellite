"""System volume packet support."""

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "systemvolume"
    display_name = "System volume"
    description = "Receive volume status from the paired device."
    icon = "audio-volume-high-symbolic"
    incoming_capabilities = ("kdeconnect.systemvolume",)
    outgoing_capabilities = ("kdeconnect.systemvolume.request",)
    packet_types = incoming_capabilities
