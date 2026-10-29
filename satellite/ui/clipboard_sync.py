"""Keeps this computer's clipboard and connected devices' clipboards in step."""

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

# Password managers (KeePassXC, KWallet, ...) offer this target with "secret".
PASSWORD_HINT = "x-kde-passwordManagerHint"


class ClipboardSync:
    def __init__(self, daemon):
        self._daemon = daemon
        self._clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self._last_text = None
        self._sent = {}
        self._received = {}

    def start(self, interval_ms=1000):
        GLib.timeout_add(interval_ms, self._tick)

    def set_text(self, text):
        """Put text on the local clipboard without echoing it back to devices."""
        self._last_text = text
        self._clipboard.set_text(text, -1)
        for device in self._daemon.devices():
            self._sent[device["id"]] = text
        return GLib.SOURCE_REMOVE

    def on_devices(self, devices):
        for device in devices:
            data = device.get("plugin_data", {}).get("clipboard", {})
            content = data.get("body", {}).get("content")
            signature = (data.get("id"), content)
            if isinstance(content, str) and signature != self._received.get(device["id"]):
                self._received[device["id"]] = signature
                self._last_text = content
                self._clipboard.set_text(content, -1)
                self._sent[device["id"]] = content

    def _tick(self):
        content = self._clipboard.wait_for_text()
        if content is None:
            return GLib.SOURCE_CONTINUE
        self._last_text = content
        is_password = None  # only asked once something would actually be sent
        for device in self._daemon.devices():
            device_id = device["id"]
            plugin = next((p for p in device.get("available_plugins", [])
                           if p.get("id") == "clipboard"), None)
            settings = self._daemon.plugin_settings(device_id, "clipboard")
            if (
                not device.get("connected") or not plugin or not plugin.get("enabled")
                or self._sent.get(device_id) == content
                or not settings.get("send", True)
            ):
                continue
            if not settings.get("send_passwords", True):
                if is_password is None:
                    is_password = self._is_password()
                if is_password:
                    self._sent[device_id] = content  # handled: don't ask again
                    continue
            if self._daemon.send_plugin_packet(
                "clipboard", device_id, "kdeconnect.clipboard", {"content": content}
            ):
                self._sent[device_id] = content
        return GLib.SOURCE_CONTINUE

    def _is_password(self):
        found, targets = self._clipboard.wait_for_targets()
        return bool(found) and any(target.name() == PASSWORD_HINT for target in targets or ())
