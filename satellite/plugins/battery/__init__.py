"""Show the paired device's battery and share this computer's battery."""

import glob
import threading
import time

from satellite.i18n import _
from satellite.system_battery import SystemBatteries

ENABLED_BY_DEFAULT = True
LOW_BATTERY_PERCENT = 15
REPORT_INTERVAL = 60


def read_local_battery(root="/sys/class/power_supply"):
    """Return (percent, charging) for the first laptop battery, else None."""
    for path in sorted(glob.glob(root + "/BAT*")):
        try:
            with open(path + "/capacity", encoding="ascii") as stream:
                percent = int(stream.read().strip())
            with open(path + "/status", encoding="ascii") as stream:
                status = stream.read().strip()
        except (OSError, ValueError):
            continue
        return max(0, min(100, percent)), status in ("Charging", "Full")
    return None


class Plugin:
    plugin_id = "battery"
    display_name = _("Battery")
    description = _("Show the paired device's battery, and share this computer's battery with it.")
    icon = "battery-good-symbolic"
    incoming_capabilities = ("kdeconnect.battery",)
    outgoing_capabilities = ("kdeconnect.battery",)
    settings_schema = (
        {"key": "notify_low", "label": _("Warn when the device battery is low"), "type": "bool", "default": True},
        {"key": "share", "label": _("Share this computer's battery"), "type": "bool", "default": True},
        {"key": "system", "label": _("Show the device's battery in the system's power menu and System Monitor"),
         "type": "bool", "default": True},
    )

    def __init__(self, reader=read_local_battery, system=None):
        self._reader = reader
        self._system = system or SystemBatteries()
        self._low = set()
        self._last_sent = {}
        self._thread = None
        self._lock = threading.Lock()

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.battery" in remote_outgoing or "kdeconnect.battery" in remote_incoming

    def on_connected(self, daemon, device_id):
        self._last_sent.pop(device_id, None)
        self._report(daemon, device_id)
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._loop, args=(daemon,), daemon=True
                )
                self._thread.start()

    def settings_changed(self, daemon, device_id):
        self._last_sent.pop(device_id, None)
        self._report(daemon, device_id)
        if not daemon.plugins.get_settings(device_id, self.plugin_id).get("system", True):
            self._system.remove(device_id)

    def on_disconnected(self, _daemon, device_id):
        self._system.remove(device_id)

    def shutdown(self, _daemon):
        self._system.remove_all()

    def _loop(self, daemon):
        while True:
            time.sleep(REPORT_INTERVAL)
            for device in daemon.devices():
                if device.get("connected"):
                    self._report(daemon, device["id"])

    def _report(self, daemon, device_id):
        reading = self._reader()
        settings = daemon.plugins.get_settings(device_id, self.plugin_id)
        if reading is None or not settings.get("share", True):
            return False
        percent, charging = reading
        if self._last_sent.get(device_id) == reading:
            return False
        sent = daemon.send_plugin_packet(
            self.plugin_id, device_id, "kdeconnect.battery",
            {
                "currentCharge": percent,
                "isCharging": charging,
                "thresholdEvent": 1 if percent <= LOW_BATTERY_PERCENT and not charging else 0,
            },
        )
        if sent:
            self._last_sent[device_id] = reading
        return sent

    def handle_packet(self, daemon, link, packet):
        if packet.get("type") != "kdeconnect.battery":
            return False
        body = packet["body"]
        charge = body.get("currentCharge")
        if isinstance(charge, (int, float)) and not isinstance(charge, bool) and -1 <= charge <= 100:
            charging = bool(body.get("isCharging", False))
            with daemon._lock:
                device = daemon._devices.get(link["id"])
                if device:
                    device["battery"] = charge
                    device["isCharging"] = charging
                    device["last_seen"] = time.monotonic()
            daemon._notify_devices_changed()
            self._check_low(daemon, link, charge, charging)
            if daemon.plugins.get_settings(link["id"], self.plugin_id).get("system", True):
                self._system.update(link["id"], link["name"], charge, charging)
        return True

    def _check_low(self, daemon, link, charge, charging):
        settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
        low = 0 <= charge <= LOW_BATTERY_PERCENT and not charging
        if low and link["id"] not in self._low:
            self._low.add(link["id"])
            if settings.get("notify_low", True):
                daemon.notify(
                    _("{} battery is low").format(link["name"]),
                    # xgettext:no-python-format
                    _("{}% remaining. Plug it in soon.").format(int(charge)),
                    icon="battery-caution-symbolic", timeout=10000,
                )
        elif not low:
            self._low.discard(link["id"])

    @staticmethod
    def send(daemon, device_id, packet_type=None, body=None, **_kwargs):
        if packet_type not in Plugin.outgoing_capabilities:
            return False
        return daemon._send_plugin_packet(device_id, packet_type, body or {})
