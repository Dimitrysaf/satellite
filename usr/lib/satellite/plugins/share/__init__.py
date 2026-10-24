"""Text, URL, and file share packet support."""

from .._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "share"
    display_name = "Share"
    description = "Receive shared text, URLs, and transfer requests."
    icon = "document-send-symbolic"
    incoming_capabilities = (
        "kdeconnect.share.request",
        "kdeconnect.share.request.update",
    )
    outgoing_capabilities = ("kdeconnect.share.request",)
    packet_types = incoming_capabilities

    @classmethod
    def handle_packet(cls, daemon, link, packet):
        if packet.get("type") not in cls.packet_types:
            return False
        body = dict(packet.get("body", {}))
        if packet.get("payloadSize") is not None:
            received_path = daemon.receive_share_payload(link, packet)
            if received_path:
                body["receivedFile"] = received_path
            else:
                body["transferError"] = "File transfer failed"
        packet = dict(packet)
        packet["body"] = body
        return super().handle_packet(daemon, link, packet)
