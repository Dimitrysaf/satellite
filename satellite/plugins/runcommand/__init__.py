"""Remote command list packet support."""

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "runcommand"
    display_name = "Run commands"
    description = "Receive the paired device's available command list."
    icon = "utilities-terminal-symbolic"
    incoming_capabilities = ("kdeconnect.runcommand", "kdeconnect.runcommand.output")
    outgoing_capabilities = ("kdeconnect.runcommand.request",)
    packet_types = incoming_capabilities
