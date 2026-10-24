"""Contacts packet support."""

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "contacts"
    display_name = "Contacts"
    description = "Receive contact UID and vCard responses."
    icon = "contact-new-symbolic"
    incoming_capabilities = (
        "kdeconnect.contacts.response_all_uids_timestamps",
        "kdeconnect.contacts.response_uids_timestamps",
        "kdeconnect.contacts.response_vcards",
    )
    outgoing_capabilities = (
        "kdeconnect.contacts.request_all_uids_timestamps",
        "kdeconnect.contacts.request_vcards_by_uid",
    )
    packet_types = incoming_capabilities
    settings_schema = ({"key": "receive", "label": "Receive contact data", "type": "bool", "default": True},)
