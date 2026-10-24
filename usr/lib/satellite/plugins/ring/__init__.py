"""Find My Phone plugin for ringing a paired remote device."""

ENABLED_BY_DEFAULT = True


class Plugin:
    display_name = "Ring"
    description = "Ring the paired device to help locate it."
    icon = "phone-ringing-symbolic"
    incoming_capabilities = ("kdeconnect.findmyphone.request",)
    outgoing_capabilities = ("kdeconnect.findmyphone.request",)

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        capability = "kdeconnect.findmyphone.request"
        return capability in remote_incoming or capability in remote_outgoing

    @staticmethod
    def handle_packet(daemon, link, packet):
        if packet.get("type") != "kdeconnect.findmyphone.request":
            return False
        with daemon._lock:
            device = daemon._devices.get(link["id"])
            if device:
                device["ring_requested"] = not device.get("ring_requested", False)
        daemon._notify_devices_changed()
        return True

    @staticmethod
    def send(daemon, device_id):
        return daemon._send_plugin_packet(
            device_id, "kdeconnect.findmyphone.request", {}
        )
