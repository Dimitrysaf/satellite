"""Turns transfer progress into a popup, a notification and a taskbar bar."""

import os

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("XApp", "1.0")
from gi.repository import Gio, GLib, XApp

from satellite.ui.transfer_popup import TransferPopup
from satellite.ui.widgets import format_bytes
from satellite.i18n import _


class TransferTracker:
    def __init__(self, window, notifier):
        self._window = window
        self._notifier = notifier
        self._popups = {}
        self._notification_ids = {}
        self._active = {}

    def update(self, progress):
        """Called on the main thread for every progress report."""
        transfer_id = progress["id"]
        popup = self._popups.get(transfer_id)
        if popup is None:
            popup = self._popups[transfer_id] = TransferPopup()
        popup.update(progress)

        if progress.get("finished"):
            self._active.pop(transfer_id, None)
            GLib.timeout_add_seconds(6, self._close_popup, transfer_id)
        else:
            self._active[transfer_id] = progress.get("percent", 0)
        XApp.set_window_progress(self._window, max(self._active.values(), default=0))
        self._notify(progress)
        return GLib.SOURCE_REMOVE

    def clear(self):
        XApp.set_window_progress(self._window, 0)

    def _close_popup(self, transfer_id):
        popup = self._popups.pop(transfer_id, None)
        if popup:
            popup.destroy()
        return GLib.SOURCE_REMOVE

    def _notify(self, progress):
        transfer_id = progress["id"]
        sending = progress.get("direction") == "send"
        name = progress["filename"]
        received = format_bytes(progress["received"])
        total = format_bytes(progress["total"])
        percent = max(0, progress.get("percent", 0))
        actions = []
        if progress.get("error"):
            summary, body, timeout = _("File transfer failed"), "{} · {}".format(name, progress["error"]), 8000
        elif progress.get("finished"):
            summary = _("File sent") if sending else _("File received")
            if sending:
                body = _("{} · sent to {}").format(name, progress["device_name"])
            else:
                folder = os.path.dirname(progress["path"] or "")
                body = _("{} · saved to {}").format(name, folder)
                if folder:
                    actions = [("open", _("Open folder"), lambda f=folder: Gio.AppInfo.launch_default_for_uri(
                        GLib.filename_to_uri(f, None), None))]
            timeout = 6000
        else:
            summary = (_("Sending file to {}") if sending else _("Receiving file from {}")).format(
                progress["device_name"])
            body, timeout = "{} · {} / {} · {}%".format(name, received, total, percent), 0
        notification_id = self._notifier.notify(
            summary, body, icon="folder-download-symbolic",
            actions=actions, replaces=self._notification_ids.get(transfer_id, 0),
            timeout=timeout, progress=percent,
        )
        if progress.get("finished"):
            self._notification_ids.pop(transfer_id, None)
        elif notification_id:
            self._notification_ids[transfer_id] = notification_id
