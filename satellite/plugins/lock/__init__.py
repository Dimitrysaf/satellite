"""Device lock status packet support."""

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "lock"
    display_name = "Lock status"
    description = "Receive lock status updates from the paired device."
    icon = "system-lock-screen-symbolic"
    incoming_capabilities = ("kdeconnect.lock",)
    outgoing_capabilities = ("kdeconnect.lock.request",)
    packet_types = incoming_capabilities
