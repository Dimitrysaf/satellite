"""Telephony event packet support."""

from .._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "telephony"
    display_name = "Telephony"
    description = "Receive incoming, missed, and active call events."
    icon = "phone-symbolic"
    incoming_capabilities = ("kdeconnect.telephony",)
    outgoing_capabilities = ("kdeconnect.telephony.request_mute",)
    packet_types = incoming_capabilities
