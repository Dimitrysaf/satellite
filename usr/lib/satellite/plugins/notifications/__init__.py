"""Notification packet support."""

from .._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "notifications"
    display_name = "Notifications"
    description = "Receive and store notifications from the paired device."
    icon = "notifications-symbolic"
    incoming_capabilities = ("kdeconnect.notification",)
    outgoing_capabilities = (
        "kdeconnect.notification.request",
        "kdeconnect.notification.action",
        "kdeconnect.notification.reply",
    )
    packet_types = incoming_capabilities
