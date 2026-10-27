"""Tiny stand-ins for the daemon so plugins can be tested without sockets."""

import threading


class FakePlugins:
    def __init__(self, settings=None):
        self.settings = settings or {}

    def get_settings(self, device_id, plugin_id):
        return dict(self.settings.get(plugin_id, {}))


class FakeDaemon:
    def __init__(self, settings=None, devices=None):
        self.plugins = FakePlugins(settings)
        self._lock = threading.RLock()
        self._devices = devices or {}
        self.notifications = []
        self.sent = []
        self.clipboard = []
        self.urls = []
        self.progress = []
        self.on_transfer_progress = self.progress.append
        self.changed = 0

    def _notify_devices_changed(self):
        self.changed += 1

    def devices(self):
        return list(self._devices.values())

    def notify(self, summary, body="", icon="", actions=None, replaces=0, timeout=-1, progress=None):
        self.notifications.append({
            "summary": summary, "body": body, "icon": icon,
            "actions": actions or [], "replaces": replaces,
        })
        return len(self.notifications)

    def send_plugin_packet(self, plugin_id, device_id, packet_type, body=None):
        self.sent.append((plugin_id, device_id, packet_type, body or {}))
        return True

    def set_clipboard(self, text):
        self.clipboard.append(text)

    def open_url(self, url):
        self.urls.append(url)


LINK = {"id": "dev1", "name": "Pixel 7", "address": "127.0.0.1"}


def packet(packet_type, body=None, **extra):
    return dict({"id": 1, "type": packet_type, "body": body or {}}, **extra)
