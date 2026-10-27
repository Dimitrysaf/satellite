"""Shared helpers for KDE Connect plugins.

A plugin lives in one folder, ``satellite/plugins/<id>/__init__.py``, and owns
everything about its feature: packet handling, desktop integration, the
buttons it adds to the device page and its own configuration dialog.

Optional hooks the window and manager understand (all duck-typed):

* ``actions(device)``      -> list of :class:`PluginAction` shown as buttons
* ``status(device)``       -> ``(icon_name, text)`` or ``None`` for a status row
* ``configure(ctx, settings)`` -> new settings dict, or ``None`` if cancelled
  (a plugin that defines this draws its own dialog; GTK must be imported
  lazily inside it so the daemon stays headless)
* ``on_connected(daemon, device_id)``
* ``settings_changed(daemon, device_id)``
"""


class PluginAction:
    """A button a plugin adds to the device page."""

    def __init__(self, action_id, label, icon, callback, tooltip=None):
        self.id = action_id
        self.label = label
        self.icon = icon
        self.callback = callback  # callback(ctx) -> None
        self.tooltip = tooltip or label


class PacketStatePlugin:
    packet_types = ()
    incoming_capabilities = ()
    outgoing_capabilities = ()
    settings_schema = ()

    @classmethod
    def is_supported(cls, remote_incoming, remote_outgoing):
        return (
            any(item in remote_outgoing for item in cls.incoming_capabilities)
            or any(item in remote_incoming for item in cls.outgoing_capabilities)
        )

    @classmethod
    def handle_packet(cls, daemon, link, packet):
        packet_type = packet.get("type")
        if packet_type not in cls.packet_types:
            return False
        settings = daemon.plugins.get_settings(link["id"], cls.plugin_id)
        for field in cls.settings_schema:
            if field.get("key") == "receive" and not settings.get("receive", True):
                return True
        with daemon._lock:
            device = daemon._devices.get(link["id"])
            if device:
                plugin_data = device.setdefault("plugin_data", {})
                plugin_data[cls.plugin_id] = {
                    "type": packet_type,
                    "id": packet.get("id"),
                    "body": packet["body"],
                }
        daemon._notify_devices_changed()
        return True

    @classmethod
    def send(cls, daemon, device_id, packet_type=None, body=None, **_kwargs):
        if packet_type not in cls.outgoing_capabilities:
            return False
        return daemon._send_plugin_packet(device_id, packet_type, body or {})

    def actions(self, device):
        return ()

    def status(self, device):
        return None
