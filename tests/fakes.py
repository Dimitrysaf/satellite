"""Tiny stand-ins for the daemon so plugins can be tested without sockets."""

import threading


class FakePlugins:
    def __init__(self, settings=None, disabled=()):
        self.settings = settings or {}
        self.disabled = set(disabled)

    def get_settings(self, device_id, plugin_id):
        return dict(self.settings.get(plugin_id, {}))

    def is_enabled_for(self, device_id, plugin_id):
        return plugin_id not in self.disabled


class FakeDaemon:
    def __init__(self, settings=None, devices=None, disabled=()):
        self.plugins = FakePlugins(settings, disabled)
        self._lock = threading.RLock()
        self._devices = devices or {}
        self.notifications = []
        self.sent = []
        self.clipboard = []
        self.urls = []
        self.events = []
        self.closed = []
        self.prompts = []
        self.progress = []
        self.on_transfer_progress = self.progress.append
        self.changed = 0

    def _notify_devices_changed(self):
        self.changed += 1

    def devices(self):
        return list(self._devices.values())

    def notify(self, summary, body="", icon="", actions=None, replaces=0, timeout=-1, progress=None,
               on_closed=None, **hints):
        self.notifications.append(dict({
            "summary": summary, "body": body, "icon": icon,
            "actions": actions or [], "replaces": replaces, "on_closed": on_closed,
        }, **hints))
        return len(self.notifications)

    def close_notification(self, notification_id):
        self.closed.append(notification_id)

    def prompt_text(self, title, label, callback):
        self.prompts.append((title, callback))
        return True

    def send_plugin_packet(self, plugin_id, device_id, packet_type, body=None):
        self.sent.append((plugin_id, device_id, packet_type, body or {}))
        return True

    def set_clipboard(self, text):
        self.clipboard.append(text)

    def open_url(self, url):
        self.urls.append(url)

    def emit_event(self, name, device_id, payload=None):
        self.events.append((name, device_id, payload))

    def set_plugin_enabled(self, device_id, plugin_id, enabled):
        (self.plugins.disabled.discard if enabled else self.plugins.disabled.add)(plugin_id)
        return True

    def set_plugin_settings(self, device_id, plugin_id, settings):
        self.plugins.settings[plugin_id] = dict(settings)
        return True


LINK = {"id": "dev1", "name": "Pixel 7", "address": "127.0.0.1"}


def packet(packet_type, body=None, **extra):
    return dict({"id": 1, "type": packet_type, "body": body or {}}, **extra)
