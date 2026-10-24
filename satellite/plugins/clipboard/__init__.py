"""Clipboard packet support."""

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "clipboard"
    display_name = "Clipboard"
    description = "Receive clipboard text from the paired device."
    icon = "edit-paste-symbolic"
    incoming_capabilities = ("kdeconnect.clipboard", "kdeconnect.clipboard.connect")
    outgoing_capabilities = ("kdeconnect.clipboard",)
    packet_types = incoming_capabilities
    settings_schema = ({"key": "receive", "label": "Receive clipboard updates", "type": "bool", "default": True},)
