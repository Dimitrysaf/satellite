"""SFTP negotiation packet support."""

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "sftp"
    display_name = "Browse files (SFTP)"
    description = "Receive SFTP session details from the paired device."
    icon = "folder-remote-symbolic"
    incoming_capabilities = ("kdeconnect.sftp",)
    outgoing_capabilities = ("kdeconnect.sftp.request",)
    packet_types = incoming_capabilities
