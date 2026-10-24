"""Ping plugin."""

ENABLED_BY_DEFAULT = True


class Plugin:
    display_name = "Ping"
    description = "Send and receive short ping messages."
    icon = "mail-send-symbolic"
    incoming_capabilities = ("kdeconnect.ping",)
    outgoing_capabilities = ("kdeconnect.ping",)
    settings_schema = ({"key": "message", "label": "Default ping message", "type": "text", "default": "Ping from Satellite"},)

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return (
            "kdeconnect.ping" in remote_incoming
            or "kdeconnect.ping" in remote_outgoing
        )

    @staticmethod
    def handle_packet(daemon, link, packet):
        if packet.get("type") != "kdeconnect.ping":
            return False
        body = packet["body"]
        with daemon._lock:
            device = daemon._devices.get(link["id"])
            if device:
                message = body.get("message", "")
                device["last_ping"] = message if isinstance(message, str) else ""
        daemon._notify_devices_changed()
        return True

    @staticmethod
    def send(daemon, device_id, message="Ping from Satellite"):
        return daemon._send_plugin_packet(
            device_id, "kdeconnect.ping", {"message": message}
        )
