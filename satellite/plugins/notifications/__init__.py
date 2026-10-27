"""Mirror notifications from the paired device onto this desktop."""

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "notifications"
    display_name = "Notifications"
    description = "Show the paired device's notifications on this computer."
    icon = "notifications-symbolic"
    incoming_capabilities = ("kdeconnect.notification",)
    outgoing_capabilities = (
        "kdeconnect.notification.request",
        "kdeconnect.notification.action",
        "kdeconnect.notification.reply",
    )
    packet_types = incoming_capabilities
    settings_schema = (
        {"key": "show", "label": "Show notifications from this device", "type": "bool", "default": True},
        {"key": "show_content", "label": "Show notification text (turn off for privacy)", "type": "bool", "default": True},
    )

    def __init__(self):
        self._shown = {}

    def on_connected(self, daemon, device_id):
        daemon.send_plugin_packet(
            self.plugin_id, device_id, "kdeconnect.notification.request", {"request": True}
        )

    def handle_packet(self, daemon, link, packet):
        if packet.get("type") != "kdeconnect.notification":
            return False
        body = packet.get("body", {})
        key = (link["id"], str(body.get("id", "")))
        settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
        if body.get("isCancel"):
            self._shown.pop(key, None)
            return True
        if not settings.get("show", True) or body.get("silent"):
            return True

        app = str(body.get("appName") or "Notification")
        title = str(body.get("title") or "")
        text = str(body.get("text") or body.get("ticker") or "")
        if settings.get("show_content", True):
            summary = "{}: {}".format(app, title) if title else app
        else:
            summary, text = app, "New notification on {}".format(link["name"])

        actions = []
        for index, name in enumerate(body.get("actions") or ()):
            if isinstance(name, str) and name:
                actions.append((
                    "a{}".format(index), name,
                    lambda name=name, id=body.get("id"): daemon.send_plugin_packet(
                        self.plugin_id, link["id"], "kdeconnect.notification.action",
                        {"key": id, "action": name},
                    ),
                ))
        notification_id = daemon.notify(
            summary, text, icon="notifications-symbolic", actions=actions,
            replaces=self._shown.get(key, 0), timeout=8000,
        )
        if notification_id:
            if len(self._shown) > 500:
                self._shown.clear()
            self._shown[key] = notification_id
        return True
