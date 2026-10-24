"""SMS and MMS packet support."""

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "sms"
    display_name = "SMS and MMS"
    description = "Receive messages and conversation updates."
    icon = "mail-message-new-symbolic"
    incoming_capabilities = (
        "kdeconnect.sms.messages",
        "kdeconnect.sms.attachment_file",
    )
    outgoing_capabilities = (
        "kdeconnect.sms.request",
        "kdeconnect.sms.request_conversations",
        "kdeconnect.sms.request_conversation",
        "kdeconnect.sms.request_attachment",
    )
    packet_types = incoming_capabilities
