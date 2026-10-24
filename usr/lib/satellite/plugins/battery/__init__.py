"""Battery status plugin."""

import time

ENABLED_BY_DEFAULT = True


class Plugin:
    display_name = "Battery"
    description = "Show the paired device's battery level and charging state."
    icon = "battery-good-symbolic"
    incoming_capabilities = ("kdeconnect.battery",)
    outgoing_capabilities = ()

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.battery" in remote_outgoing

    @staticmethod
    def handle_packet(daemon, link, packet):
        if packet.get("type") != "kdeconnect.battery":
            return False
        body = packet["body"]
        charge = body.get("currentCharge")
        if isinstance(charge, (int, float)) and not isinstance(charge, bool) and -1 <= charge <= 100:
            with daemon._lock:
                device = daemon._devices.get(link["id"])
                if device:
                    device["battery"] = charge
                    device["isCharging"] = bool(body.get("isCharging", False))
                    device["last_seen"] = time.monotonic()
            daemon._notify_devices_changed()
        return True

    @staticmethod
    def send(_daemon, _device_id, **_kwargs):
        return False
