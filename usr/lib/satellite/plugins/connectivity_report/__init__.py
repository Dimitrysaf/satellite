"""Connectivity report packet support."""

from .._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "connectivity_report"
    display_name = "Connectivity report"
    description = "Show mobile network signal reports from the device."
    icon = "network-cellular-signal-excellent-symbolic"
    incoming_capabilities = ("kdeconnect.connectivity_report",)
    outgoing_capabilities = ("kdeconnect.connectivity_report.request",)
    packet_types = incoming_capabilities
