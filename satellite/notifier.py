"""Desktop notifications over org.freedesktop.Notifications.

Only needs Gio, so the daemon and plugins can use it without GTK. Action
buttons call back into Python when the user clicks them.
"""

import gi

from gi.repository import Gio, GLib

_NAME = "org.freedesktop.Notifications"
_PATH = "/org/freedesktop/Notifications"


class Notifier:
    def __init__(self, app_name="Satellite"):
        self.app_name = app_name
        self._callbacks = {}
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._bus.signal_subscribe(
                _NAME, _NAME, "ActionInvoked", _PATH, None,
                Gio.DBusSignalFlags.NONE, self._on_action_invoked,
            )
            self._bus.signal_subscribe(
                _NAME, _NAME, "NotificationClosed", _PATH, None,
                Gio.DBusSignalFlags.NONE, self._on_closed,
            )
        except GLib.Error:
            self._bus = None

    @property
    def available(self):
        return self._bus is not None

    def notify(self, summary, body="", icon="satellite", actions=None,
               replaces=0, timeout=-1, progress=None):
        """Show or update a notification and return its id (0 on failure).

        ``actions`` is a list of ``(key, label, callback)``. ``progress`` is
        0-100 for a progress bar, or ``None``.
        """
        if self._bus is None:
            return 0
        actions = list(actions or ())
        flat = []
        for key, label, _callback in actions:
            flat.extend((key, label))
        hints = {}
        if progress is not None:
            hints["value"] = GLib.Variant("i", max(0, min(100, int(progress))))
        try:
            reply = self._bus.call_sync(
                _NAME, _PATH, _NAME, "Notify",
                GLib.Variant("(susssasa{sv}i)", (
                    self.app_name, replaces, icon or "", summary, body,
                    flat, hints, timeout,
                )),
                GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 2000, None,
            )
        except GLib.Error as error:
            print("Could not show a notification: {}".format(error), flush=True)
            return 0
        notification_id = reply.unpack()[0]
        self._callbacks[notification_id] = {
            key: callback for key, _label, callback in actions
        }
        return notification_id

    def close(self, notification_id):
        if self._bus is None or not notification_id:
            return
        try:
            self._bus.call_sync(
                _NAME, _PATH, _NAME, "CloseNotification",
                GLib.Variant("(u)", (notification_id,)), None,
                Gio.DBusCallFlags.NONE, 2000, None,
            )
        except GLib.Error:
            pass
        self._callbacks.pop(notification_id, None)

    def _on_action_invoked(self, _bus, _sender, _path, _iface, _signal, params):
        notification_id, key = params.unpack()
        callback = self._callbacks.get(notification_id, {}).get(key)
        if callback:
            try:
                callback()
            except Exception as error:
                print("Notification action failed: {!r}".format(error), flush=True)

    def _on_closed(self, _bus, _sender, _path, _iface, _signal, params):
        self._callbacks.pop(params.unpack()[0], None)
