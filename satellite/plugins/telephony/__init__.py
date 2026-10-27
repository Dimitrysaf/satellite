"""Show incoming and missed phone calls on the desktop."""

from satellite.plugins._base import PacketStatePlugin

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "telephony"
    display_name = "Telephony"
    description = "Show incoming and missed calls, with a button to silence the ringer."
    icon = "phone-symbolic"
    incoming_capabilities = ("kdeconnect.telephony",)
    outgoing_capabilities = ("kdeconnect.telephony.request_mute",)
    packet_types = incoming_capabilities
    settings_schema = (
        {"key": "notify", "label": "Show call notifications", "type": "bool", "default": True},
    )

    def __init__(self):
        self._call_notifications = {}

    def handle_packet(self, daemon, link, packet):
        handled = super().handle_packet(daemon, link, packet)
        if not handled:
            return False
        settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
        if settings.get("notify", True):
            self._notify(daemon, link, packet.get("body", {}))
        return True

    def _notify(self, daemon, link, body):
        event = body.get("event")
        number = body.get("phoneNumber") or "unknown number"
        caller = body.get("contactName") or number
        previous = self._call_notifications.get(link["id"], 0)
        if body.get("isCancel"):
            if previous:
                daemon.notify("Call ended", caller, icon="call-stop-symbolic",
                              replaces=previous, timeout=1500)
                self._call_notifications.pop(link["id"], None)
            return
        if event == "ringing":
            mute = ("mute", "Silence", lambda: daemon.send_plugin_packet(
                self.plugin_id, link["id"], "kdeconnect.telephony.request_mute", {}))
            notification_id = daemon.notify(
                "Incoming call on {}".format(link["name"]), caller,
                icon="call-start-symbolic", actions=[mute], replaces=previous,
                timeout=30000,
            )
        elif event == "missedCall":
            notification_id = daemon.notify(
                "Missed call on {}".format(link["name"]), caller,
                icon="call-missed-symbolic", replaces=previous, timeout=-1,
            )
        elif event == "talking":
            notification_id = daemon.notify(
                "On a call · {}".format(link["name"]), caller,
                icon="call-start-symbolic", replaces=previous, timeout=4000,
            )
        else:
            return
        if notification_id:
            self._call_notifications[link["id"]] = notification_id
