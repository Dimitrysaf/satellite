"""Send and receive short ping messages."""

from satellite.plugins._base import PluginAction
from satellite.i18n import _

ENABLED_BY_DEFAULT = True


class Plugin:
    plugin_id = "ping"
    display_name = _("Ping")
    description = _("Send and receive short ping messages.")
    icon = "mail-unread-symbolic"
    incoming_capabilities = ("kdeconnect.ping",)
    outgoing_capabilities = ("kdeconnect.ping",)
    settings_schema = (
        {"key": "message", "label": _("Default ping message"), "type": "text", "default": _("Ping from Satellite")},
        {"key": "notify", "label": _("Show a notification when pinged"), "type": "bool", "default": True},
    )

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.ping" in remote_incoming or "kdeconnect.ping" in remote_outgoing

    @staticmethod
    def handle_packet(daemon, link, packet):
        if packet.get("type") != "kdeconnect.ping":
            return False
        message = packet["body"].get("message", "")
        message = message if isinstance(message, str) else ""
        settings = daemon.plugins.get_settings(link["id"], "ping")
        if settings.get("notify", True):
            daemon.notify(
                _("Ping from {}").format(link["name"]), message or _("Ping!"),
                icon="mail-unread-symbolic", timeout=6000,
            )
        return True

    @staticmethod
    def send(daemon, device_id, message=_("Ping from Satellite"), **_kwargs):
        return daemon._send_plugin_packet(device_id, "kdeconnect.ping", {"message": message})

    def actions(self, device):
        def run(ctx):
            if not ctx.daemon.send_ping(ctx.device_id):
                ctx.error(
                    _("Could not send ping"),
                    _("The secure connection is no longer active or the device does not support Ping."),
                )

        return [PluginAction("ping", _("Ping"), "mail-send-symbolic", run, _("Send a ping to this device"))]
