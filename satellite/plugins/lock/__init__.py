"""Lock this computer from the paired device and report its lock state."""

import os
import subprocess

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


def _session():
    return os.environ.get("XDG_SESSION_ID") or "self"


def run_loginctl(*arguments):
    """Run loginctl and return stdout, or None if it is unavailable."""
    try:
        result = subprocess.run(
            ["loginctl", *arguments], check=True, capture_output=True,
            text=True, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout


class Plugin(PacketStatePlugin):
    plugin_id = "lock"
    display_name = "Lock"
    description = "Lock this computer from the paired device, and show whether the device is locked."
    icon = "system-lock-screen-symbolic"
    incoming_capabilities = ("kdeconnect.lock", "kdeconnect.lock.request")
    outgoing_capabilities = ("kdeconnect.lock", "kdeconnect.lock.request")
    packet_types = ("kdeconnect.lock",)
    settings_schema = (
        {"key": "allow_unlock", "label": "Allow this device to unlock the computer", "type": "bool", "default": False},
    )

    def __init__(self, loginctl=run_loginctl):
        self._loginctl = loginctl

    def is_locked(self):
        output = self._loginctl("show-session", _session(), "-p", "LockedHint", "--value")
        return bool(output) and output.strip() == "yes"

    def handle_packet(self, daemon, link, packet):
        packet_type = packet.get("type")
        if packet_type == "kdeconnect.lock":
            return super().handle_packet(daemon, link, packet)
        if packet_type != "kdeconnect.lock.request":
            return False
        body = packet.get("body", {})
        settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
        if "setLocked" in body:
            if body["setLocked"] is True:
                self._loginctl("lock-session", _session())
            elif body["setLocked"] is False and settings.get("allow_unlock", False):
                self._loginctl("unlock-session", _session())
        if "requestLocked" in body or "setLocked" in body:
            daemon.send_plugin_packet(
                self.plugin_id, link["id"], "kdeconnect.lock",
                {"isLocked": self.is_locked()},
            )
        return True

    def status(self, device):
        data = device.get("plugin_data", {}).get(self.plugin_id, {}).get("body", {})
        if "isLocked" not in data:
            return None
        if data["isLocked"]:
            return ("system-lock-screen-symbolic", "Device is locked")
        return ("changes-allow-symbolic", "Device is unlocked")
