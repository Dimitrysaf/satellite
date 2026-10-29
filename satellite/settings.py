"""Small JSON-backed application preferences (not per-plugin settings)."""

import json
import os
import threading

DEFAULTS = {
    "background": True,           # keep running in the tray when the window closes
}


class AppSettings:
    def __init__(self, config_dir):
        self._path = os.path.join(config_dir, "app.json")
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        try:
            with open(self._path, "r", encoding="utf-8") as stream:
                loaded = json.load(stream)
            if isinstance(loaded, dict):
                for key, default in DEFAULTS.items():
                    if isinstance(loaded.get(key), type(default)):
                        self._data[key] = loaded[key]
        except (OSError, ValueError):
            pass

    def get(self, key):
        with self._lock:
            value = self._data[key]
            return dict(value) if isinstance(value, dict) else value

    def set(self, key, value):
        if key not in DEFAULTS or not isinstance(value, type(DEFAULTS[key])):
            raise ValueError("Unknown or invalid setting: {}".format(key))
        with self._lock:
            self._data[key] = value
            self._save()

    def _save(self):
        temporary = self._path + ".tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(self._data, stream, indent=2, sort_keys=True)
        os.replace(temporary, self._path)
