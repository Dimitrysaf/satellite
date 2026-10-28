"""Send this computer's desktop notifications to the paired device.

Like KDE Connect, Satellite becomes a D-Bus monitor for calls to
org.freedesktop.Notifications.Notify and forwards each one it sees. Every
app that shows a notification is remembered, so each can be switched off.
"""

import json
import os
import threading
from satellite.i18n import _, pgettext

ENABLED_BY_DEFAULT = True
# KDE Connect ships this plugin switched off; the user turns it on per device.
ENABLED_FOR_DEVICE_BY_DEFAULT = False

NOTIFY_MATCH = "interface='org.freedesktop.Notifications',member='Notify'"
IMAGE_HINTS = ("image-data", "image_data", "icon_data")
PATH_HINTS = ("image-path", "image_path")
ICON_SIZE = 64
APPS_FILE = "notification-apps.json"


class KnownApps:
    """Apps seen showing notifications: name -> {"icon", "desktop"}."""

    def __init__(self, config_dir):
        self.path = os.path.join(config_dir, APPS_FILE) if config_dir else None
        self._lock = threading.Lock()
        self._apps = None

    def _load(self):
        if self._apps is None:
            try:
                with open(self.path, "r", encoding="utf-8") as stream:
                    data = json.load(stream)
                self._apps = {str(k): v for k, v in data.items() if isinstance(v, dict)}
            except (OSError, TypeError, ValueError, AttributeError):
                self._apps = {}
        return self._apps

    def all(self):
        with self._lock:
            return dict(sorted(self._load().items(), key=lambda item: item[0].casefold()))

    def see(self, notification):
        name = notification["app_name"]
        if not name or self.path is None:
            return
        desktop = notification["hints"].get("desktop-entry")
        record = {"icon": str(notification["app_icon"] or ""), "desktop": str(desktop or "")}
        with self._lock:
            apps = self._load()
            known = apps.get(name, {})
            # Keep what's known when this notification says less.
            merged = {key: record[key] or known.get(key, "") for key in record}
            if known == merged:
                return
            apps[name] = merged
            try:
                temporary = self.path + ".tmp"
                with open(temporary, "w", encoding="utf-8") as stream:
                    json.dump(apps, stream, indent=1, sort_keys=True)
                os.replace(temporary, self.path)
            except OSError:
                pass


def notification_from_call(params):
    """Unpack Notify's (susssasa{sv}i) arguments into a dict."""
    app_name, replaces_id, app_icon, summary, body, actions, hints, timeout = params
    return {
        "app_name": app_name, "replaces_id": replaces_id, "app_icon": app_icon,
        "summary": summary, "body": body, "actions": actions, "hints": hints,
        "timeout": timeout,
    }


def packet_body(notification, settings, fallback_id):
    """The kdeconnect.notification body, or None if settings filter it out."""
    hints = notification["hints"]
    if notification["app_name"] in (settings.get("muted_apps") or ()):
        return None
    if notification["timeout"] > 0 and settings.get("persistent", False):
        return None
    urgency = hints.get("urgency", -1)
    if isinstance(urgency, int) and -1 < urgency < settings.get("min_urgency", 0):
        return None
    summary = notification["summary"]
    if not summary:
        return None
    text = notification["body"] if settings.get("include_body", True) else ""
    body = {
        "id": str(notification["replaces_id"] or fallback_id),
        "appName": notification["app_name"],
        "ticker": "{}: {}".format(summary, text) if text else summary,
        "isClearable": notification["timeout"] == -1,
        "title": summary,
        "silent": False,
    }
    if text:
        body["text"] = text
    return body


def icon_png(notification):
    """PNG bytes for the notification's image or icon, or None."""
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, GLib
    except (ImportError, ValueError):
        return None
    hints = notification["hints"]
    pixbuf = None
    try:
        for key in IMAGE_HINTS:
            if key in hints:
                width, height, stride, alpha, bits, _channels, data = hints[key]
                pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                    GLib.Bytes.new(bytes(data)), GdkPixbuf.Colorspace.RGB,
                    alpha, bits, width, height, stride)
                break
        if pixbuf is None:
            source = next((hints[key] for key in PATH_HINTS if key in hints), None)
            source = source or notification["app_icon"]
            if source:
                pixbuf = _load_icon(source)
        if pixbuf is None:
            return None
        pixbuf = pixbuf.scale_simple(ICON_SIZE, ICON_SIZE, GdkPixbuf.InterpType.BILINEAR)
        ok, data = pixbuf.save_to_bufferv("png", [], [])
        return bytes(data) if ok else None
    except (GLib.Error, TypeError, ValueError):
        return None


def _load_icon(source):
    from gi.repository import GdkPixbuf, GLib
    if source.startswith("file://"):
        source = GLib.filename_from_uri(source)[0]
    if source.startswith("/"):
        return GdkPixbuf.Pixbuf.new_from_file_at_size(source, ICON_SIZE, ICON_SIZE)
    try:  # an icon name needs GTK's icon theme; the headless daemon has none
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        return Gtk.IconTheme.get_default().load_icon(source, ICON_SIZE, 0)
    except (ImportError, ValueError, AttributeError, GLib.Error):
        return None


class NotificationMonitor:
    """Watches Notify calls on the session bus and passes them to ``callback``."""

    def __init__(self, callback):
        self._callback = callback
        self._connection = None

    def start(self):
        from gi.repository import Gio, GLib
        try:
            address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            connection = Gio.DBusConnection.new_for_address_sync(
                address,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None)
            connection.add_filter(self._filter)
            connection.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus.Monitoring", "BecomeMonitor",
                GLib.Variant("(asu)", ([NOTIFY_MATCH], 0)), None,
                Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error as error:
            print("Could not watch desktop notifications: {}".format(error), flush=True)
            return False
        self._connection = connection
        return True

    @property
    def running(self):
        return self._connection is not None

    def _filter(self, _connection, message, incoming):
        from gi.repository import Gio
        if not incoming or message.get_message_type() != Gio.DBusMessageType.METHOD_CALL:
            return message  # e.g. the reply to BecomeMonitor itself
        if message.get_member() == "Notify" and message.get_signature() == "susssasa{sv}i":
            notification = notification_from_call(message.get_body().unpack())
            threading.Thread(target=self._callback, args=(notification,), daemon=True).start()
        # Monitored calls are someone else's: never let GDBus answer them.
        return None


class Plugin:
    plugin_id = "sendnotifications"
    display_name = _("Send notifications")
    description = _("Show this computer's notifications on the paired device.")
    icon = "send-to-symbolic"
    incoming_capabilities = ("kdeconnect.notification.request",)
    outgoing_capabilities = ("kdeconnect.notification",)
    settings_schema = (
        {"key": "persistent", "label": _("Persistent notifications only"), "type": "bool", "default": False},
        {"key": "include_body", "label": _("Include body"), "type": "bool", "default": True},
        {"key": "include_icon", "label": _("Include icon"), "type": "bool", "default": True},
        {"key": "min_urgency", "label": _("Minimum urgency level"), "type": "choice", "default": 0,
         "options": ((0, pgettext("urgency", "Low")), (1, pgettext("urgency", "Normal")),
                     (2, pgettext("urgency", "Critical")))},
        # Filled in with the apps seen so far when the settings open (see configure).
        {"key": "muted_apps", "label": _("Send notifications from"), "type": "apps", "default": [],
         "options": ()},
    )
    # Satellite's own notifications (including ones mirrored from the phone)
    # must not be echoed back.
    ignored_apps = ("Satellite",)

    def __init__(self, monitor_factory=NotificationMonitor, icon_loader=icon_png):
        self._monitor_factory = monitor_factory
        self._icon_loader = icon_loader
        self._monitor = None
        self._daemon = None
        self._next_id = 0
        self._lock = threading.Lock()
        self._apps = None  # KnownApps, once the daemon's config folder is known

    def known_apps(self, daemon):
        with self._lock:
            if self._apps is None:
                self._apps = KnownApps(getattr(daemon, "config_dir", None))
            return self._apps

    def configure(self, ctx, settings):
        from satellite.ui.plugin_config_dialog import run_plugin_config
        apps = self.known_apps(ctx.daemon).all()
        schema = [dict(field, options=tuple((name, info) for name, info in apps.items()))
                  if field["key"] == "muted_apps" else field for field in self.settings_schema]
        return run_plugin_config(ctx.parent, {"name": self.display_name, "settings_schema": schema}, settings)

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.notification" in remote_incoming

    def on_connected(self, daemon, device_id):
        with self._lock:
            self._daemon = daemon
            if self._monitor is None:
                self._monitor = self._monitor_factory(self.forward)
                if not self._monitor.start():
                    self._monitor = None

    @staticmethod
    def handle_packet(daemon, link, packet):
        # The device may ask for notifications; Satellite sends them as they come.
        return packet.get("type") == "kdeconnect.notification.request"

    def forward(self, notification):
        daemon = self._daemon
        if daemon is None or notification["app_name"] in self.ignored_apps:
            return
        self.known_apps(daemon).see(notification)
        with self._lock:
            self._next_id += 1
            fallback_id = "satellite-{}".format(self._next_id)
        icon = None
        for device in daemon.devices():
            device_id = device["id"]
            if not device.get("connected") or not daemon.plugins.is_enabled_for(device_id, self.plugin_id):
                continue
            settings = daemon.plugins.get_settings(device_id, self.plugin_id)
            body = packet_body(notification, settings, fallback_id)
            if body is None:
                continue
            # Like KDE Connect, the icon only goes with a new notification.
            if settings.get("include_icon", True) and not notification["replaces_id"]:
                if icon is None:
                    icon = self._icon_loader(notification) or b""
                if icon and "kdeconnect.notification" in device.get("incomingCapabilities", []):
                    from satellite.plugins.share import send_bytes
                    if send_bytes(daemon, device_id, "kdeconnect.notification", body, icon):
                        continue
            daemon.send_plugin_packet(self.plugin_id, device_id, "kdeconnect.notification", body)

    @staticmethod
    def send(daemon, device_id, packet_type=None, body=None, **_kwargs):
        if packet_type != "kdeconnect.notification":
            return False
        return daemon._send_plugin_packet(device_id, packet_type, body or {})
