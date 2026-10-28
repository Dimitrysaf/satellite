"""Start Satellite at login, minimized to the tray.

The package installs ``/etc/xdg/autostart/org.x.Satellite-autostart.desktop``.
Following the XDG autostart spec, a file with the same name in the user's
``~/.config/autostart`` overrides it: ``Hidden=true`` turns it off. This is
also what Cinnamon's Startup Applications reads and writes.
"""

import os

FILE_NAME = "org.x.Satellite-autostart.desktop"
ENTRY = """[Desktop Entry]
Type=Application
Name=Satellite
Comment=Start Satellite minimized to the system tray
Exec=satellite --background
Icon=satellite
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
"""


def _user_path():
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config, "autostart", FILE_NAME)


def _system_paths():
    dirs = os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg"
    return [os.path.join(d, "autostart", FILE_NAME) for d in dirs.split(":") if d]


def _entry_enabled(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            lines = [line.strip().lower() for line in stream]
    except OSError:
        return False
    return "hidden=true" not in lines and "x-gnome-autostart-enabled=false" not in lines


def is_enabled():
    user = _user_path()
    if os.path.exists(user):
        return _entry_enabled(user)
    return any(_entry_enabled(path) for path in _system_paths())


def set_enabled(enabled):
    user = _user_path()
    system_enabled = any(_entry_enabled(path) for path in _system_paths())
    if enabled and system_enabled:
        # The packaged entry already starts Satellite; drop the override.
        try:
            os.remove(user)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(user), exist_ok=True)
    with open(user, "w", encoding="utf-8") as stream:
        stream.write(ENTRY if enabled else ENTRY + "Hidden=true\n")
