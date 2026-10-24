"""MPRIS media status packet support."""

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "mpris"
    display_name = "Media player control"
    description = "Receive media player status and metadata."
    icon = "audio-x-generic-symbolic"
    incoming_capabilities = ("kdeconnect.mpris",)
    outgoing_capabilities = ("kdeconnect.mpris.request",)
    packet_types = incoming_capabilities

    @classmethod
    def handle_packet(cls, daemon, link, packet):
        handled = super().handle_packet(daemon, link, packet)
        body = packet.get("body", {})
        players = body.get("playerList")
        if handled and isinstance(players, list) and players:
            daemon.send_plugin_packet(
                cls.plugin_id,
                link["id"],
                "kdeconnect.mpris.request",
                {"player": players[0], "requestNowPlaying": True, "requestVolume": True},
            )
        return handled
