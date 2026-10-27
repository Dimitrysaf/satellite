"""Send and receive short ping messages."""

from satellite.plugins._base import PluginAction

ENABLED_BY_DEFAULT = True


class Plugin:
    plugin_id = "ping"
    display_name = "Ping"
    description = "Send and receive short ping messages."
    icon = "mail-send-symbolic"
    incoming_capabilities = ("kdeconnect.ping",)
    outgoing_capabilities = ("kdeconnect.ping",)
    settings_schema = (
        {"key": "message", "label": "Default ping message", "type": "text", "default": "Ping from Satellite"},
        {"key": "notify", "label": "Show a notification when pinged", "type": "bool", "default": True},
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
        with daemon._lock:
            device = daemon._devices.get(link["id"])
            if device:
                device["last_ping"] = message
        daemon._notify_devices_changed()
        settings = daemon.plugins.get_settings(link["id"], "ping")
        if settings.get("notify", True):
            daemon.notify(
                "Ping from {}".format(link["name"]), message or "Ping!",
                icon="mail-send-symbolic", timeout=6000,
            )
        return True

    @staticmethod
    def send(daemon, device_id, message="Ping from Satellite", **_kwargs):
        return daemon._send_plugin_packet(device_id, "kdeconnect.ping", {"message": message})

    def actions(self, device):
        def run(ctx):
            if not ctx.daemon.send_ping(ctx.device_id):
                ctx.error(
                    "Could not send ping",
                    "The secure connection is no longer active or the device does not support Ping.",
                )

        return [PluginAction("ping", "Ping", "mail-send-symbolic", run, "Send a ping to this device")]
