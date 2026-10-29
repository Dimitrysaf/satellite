"""Shows file transfers as a system notification (updated in place) and on the taskbar button."""

import os
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("XApp", "1.0")
from gi.repository import Gio, GLib, XApp

from satellite.ui.widgets import format_bytes
from satellite.i18n import _

UPDATE_INTERVAL = 1.0  # seconds between progress updates of one notification


def progress_text(progress):
    """The progress line, for notification servers that don't draw a bar."""
    return _("{}% · {} of {}").format(
        max(0, min(100, progress.get("percent", 0))),
        format_bytes(progress["received"]), format_bytes(progress["total"]))


class TransferTracker:
    def __init__(self, window, notifier):
        self._window = window
        self._notifier = notifier
        self._notification_ids = {}
        self._last_update = {}
        self._active = {}

    def update(self, progress):
        """Called on the main thread for every progress report."""
        transfer_id = progress["id"]
        done = bool(progress.get("finished") or progress.get("error"))
        if done:
            self._active.pop(transfer_id, None)
        else:
            self._active[transfer_id] = progress.get("percent", 0)
        XApp.set_window_progress(self._window, max(self._active.values(), default=0))

        now = time.monotonic()
        if not done and now - self._last_update.get(transfer_id, 0) < UPDATE_INTERVAL:
            return GLib.SOURCE_REMOVE  # a notification redrawn many times a second just flickers
        self._last_update[transfer_id] = now
        self._notify(progress, done)
        if done:
            self._last_update.pop(transfer_id, None)
        return GLib.SOURCE_REMOVE

    def clear(self):
        XApp.set_window_progress(self._window, 0)

    def _notify(self, progress, done):
        transfer_id = progress["id"]
        sending = progress.get("direction") == "send"
        name = progress["filename"]
        actions, bar = [], None
        if progress.get("error"):
            summary, body, timeout = _("File transfer failed"), "{}\n{}".format(name, progress["error"]), 8000
        elif progress.get("finished"):
            summary = (_("Sent to {}") if sending else _("Received from {}")).format(progress["device_name"])
            body = name
            if not sending:
                folder = os.path.dirname(progress["path"] or "")
                if folder:
                    body = _("{}\nSaved to {}").format(name, folder)
                    actions = [("open", _("Open folder"), lambda f=folder: Gio.AppInfo.launch_default_for_uri(
                        GLib.filename_to_uri(f, None), None))]
            timeout = 6000
        else:
            summary = (_("Sending to {}") if sending else _("Receiving from {}")).format(progress["device_name"])
            if self._notifier.shows_progress_bars:
                body, bar = name, progress.get("percent", 0)
            else:
                body = "{}\n{}".format(name, progress_text(progress))
            timeout = 0
        notification_id = self._notifier.notify(
            summary, body, icon="satellite", actions=actions,
            replaces=self._notification_ids.get(transfer_id, 0),
            timeout=timeout, progress=bar, sound=done,
        )
        if done:
            self._notification_ids.pop(transfer_id, None)
        elif notification_id:
            self._notification_ids[transfer_id] = notification_id
