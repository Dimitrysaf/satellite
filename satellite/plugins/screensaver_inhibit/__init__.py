"""Keep the screensaver away while the paired device is connected.

Like KDE Connect's screensaver-inhibit (also off until switched on per
device). Uses org.freedesktop.ScreenSaver, or the session manager's
idle inhibitor, which Cinnamon provides.
"""

import threading
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = False

REASON = _("A device is connected with Satellite")


class SessionInhibitor:
    """Inhibit idle over D-Bus; returns a release callable, or None."""

    def inhibit(self):
        from gi.repository import Gio, GLib
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return None
        attempts = (
            ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver",
             "Inhibit", GLib.Variant("(ss)", ("Satellite", REASON)), "UnInhibit"),
            ("org.gnome.SessionManager", "/org/gnome/SessionManager", "org.gnome.SessionManager",
             "Inhibit", GLib.Variant("(susu)", ("org.x.Satellite", 0, REASON, 8)), "Uninhibit"),
        )
        for name, path, iface, method, args, release in attempts:
            try:
                cookie = bus.call_sync(name, path, iface, method, args, GLib.VariantType.new("(u)"),
                                       Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
            except GLib.Error:
                continue

            def undo(name=name, path=path, iface=iface, release=release, cookie=cookie):
                try:
                    bus.call_sync(name, path, iface, release, GLib.Variant("(u)", (cookie,)), None,
                                  Gio.DBusCallFlags.NONE, 2000, None)
                except GLib.Error:
                    pass
            return undo
        return None


class Plugin:
    plugin_id = "screensaver_inhibit"
    display_name = _("Prevent screensaver")
    description = _("Keep the screen awake while the paired device is connected.")
    icon = "preferences-desktop-screensaver-symbolic"
    incoming_capabilities = ()
    outgoing_capabilities = ()

    def __init__(self, inhibitor=None):
        self._inhibitor = inhibitor or SessionInhibitor()
        self._releases = {}
        self._lock = threading.Lock()

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return True

    @staticmethod
    def handle_packet(daemon, link, packet):
        return False

    def on_connected(self, daemon, device_id):
        with self._lock:
            if device_id in self._releases:
                return
            release = self._inhibitor.inhibit()
            if release:
                self._releases[device_id] = release

    def on_disconnected(self, daemon, device_id):
        with self._lock:
            release = self._releases.pop(device_id, None)
        if release:
            release()
