"""Show incoming and missed phone calls on the desktop, with the caller's photo."""

import base64
import binascii
import hashlib
import os

from satellite.plugins._base import PacketStatePlugin
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True
MAX_PHOTO = 512 * 1024


def photo_dir():
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(cache, "satellite", "caller-photos")


def caller_photo(body):
    """The contact photo the phone sent (phoneThumbnail, base64 JPEG) as a file, or None."""
    encoded = body.get("phoneThumbnail")
    if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_PHOTO * 4 // 3 + 4:
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not data.startswith(b"\xff\xd8"):  # JPEG, as KDE Connect expects
        return None
    path = os.path.join(photo_dir(), hashlib.sha1(data).hexdigest() + ".jpg")
    if not os.path.exists(path):
        try:
            os.makedirs(photo_dir(), mode=0o700, exist_ok=True)
            with open(path, "wb") as stream:
                stream.write(data)
        except OSError:
            return None
    return path


class Plugin(PacketStatePlugin):
    plugin_id = "telephony"
    display_name = _("Telephony")
    description = _("Show incoming and missed calls, with a button to silence the ringer.")
    icon = "phone-symbolic"
    incoming_capabilities = ("kdeconnect.telephony",)
    outgoing_capabilities = ("kdeconnect.telephony.request_mute",)
    packet_types = incoming_capabilities
    settings_schema = (
        {"key": "notify", "label": _("Show call notifications"), "type": "bool", "default": True},
        {"key": "photo", "label": _("Show the caller's photo"), "type": "bool", "default": True},
    )

    def __init__(self):
        self._call_notifications = {}

    def handle_packet(self, daemon, link, packet):
        handled = super().handle_packet(daemon, link, packet)
        if not handled:
            return False
        settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
        if settings.get("notify", True):
            self._notify(daemon, link, packet.get("body", {}), settings.get("photo", True))
        return True

    def _notify(self, daemon, link, body, photo=True):
        event = body.get("event")
        picture = caller_photo(body) if photo else None
        number = body.get("phoneNumber") or _("unknown number")
        caller = body.get("contactName") or number
        previous = self._call_notifications.get(link["id"], 0)
        if body.get("isCancel"):
            if previous:
                daemon.notify(_("Call ended"), caller, icon="call-stop-symbolic",
                              replaces=previous, timeout=1500)
                self._call_notifications.pop(link["id"], None)
            return
        if event == "ringing":
            mute = ("mute", _("Silence"), lambda: daemon.send_plugin_packet(
                self.plugin_id, link["id"], "kdeconnect.telephony.request_mute", {}))
            notification_id = daemon.notify(
                _("Incoming call on {}").format(link["name"]), caller,
                icon=picture or "call-start-symbolic", actions=[mute], replaces=previous,
                timeout=30000,
            )
        elif event == "missedCall":
            notification_id = daemon.notify(
                _("Missed call on {}").format(link["name"]), caller,
                icon=picture or "call-missed-symbolic", replaces=previous, timeout=-1,
            )
        elif event == "talking":
            notification_id = daemon.notify(
                _("On a call · {}").format(link["name"]), caller,
                icon=picture or "call-start-symbolic", replaces=previous, timeout=4000,
            )
        else:
            return
        if notification_id:
            self._call_notifications[link["id"]] = notification_id
