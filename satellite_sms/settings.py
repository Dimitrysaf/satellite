"""Satellite Messages preferences (~/.config/satellite/messages.json)."""

import json
import os

DEFAULTS = {
    "google_messages": False,  # show Google Messages for web instead of Satellite's own view
}


def _path():
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config, "satellite", "messages.json")


class MessagesSettings:
    def __init__(self, path=None):
        self.path = path or _path()
        self._data = dict(DEFAULTS)
        try:
            with open(self.path, "r", encoding="utf-8") as stream:
                loaded = json.load(stream)
            for key, default in DEFAULTS.items():
                if isinstance(loaded.get(key), type(default)):
                    self._data[key] = loaded[key]
        except (OSError, ValueError, AttributeError):
            pass

    def get(self, key):
        return self._data[key]

    def set(self, key, value):
        if key not in DEFAULTS or not isinstance(value, type(DEFAULTS[key])):
            raise ValueError("Unknown or invalid setting: {}".format(key))
        self._data[key] = value
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(self._data, stream, indent=2, sort_keys=True)
        os.replace(temporary, self.path)
