"""Mirror notifications from the paired device onto this desktop.

Like KDE Connect: the phone's action buttons work, notifications that allow
it can be answered with Reply, dismissing one side dismisses the other, app
icons and pictures come along, and one-time codes can be copied. Chats show
their recent messages. Everything goes through the desktop's own notifications
(Cinnamon keeps them in its notification list); the ones already on the phone
when it connects come quietly, without a sound.
"""

import os
import re
import threading

from satellite.plugins._base import PacketStatePlugin
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

# A 4-10 digit number on its own, as KDE Connect looks for verification codes.
CODE = re.compile(r"\b(\d{4,10})\b")
HASH = re.compile(r"^[0-9A-Za-z]{8,128}$")
MAX_MESSAGES = 6  # of a chat, newest last


def icon_dir():
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(cache, "satellite", "notification-icons")


def describe(body):
    """(app, title, text, group) for a notification body; chats show their recent messages."""
    app = str(body.get("appName") or _("Notification"))
    title = str(body.get("title") or "")
    text = str(body.get("text") or body.get("ticker") or "")
    group = str(body.get("groupName") or "")
    messages = [m for m in body.get("conversation") or () if isinstance(m, dict)]
    if messages:
        title = group or str(messages[0].get("sender") or title)
        lines = []
        for message in messages[-MAX_MESSAGES:]:
            sender, content = str(message.get("sender") or ""), str(message.get("content") or "")
            # Like KDE: no name in front of the person the chat is with.
            lines.append(content if not sender or sender == title else "{}: {}".format(sender, content))
        text = "\n".join(lines)
    return app, title, text, group


def verification_code(text):
    """A one-time code in the text, if it reads like one."""
    if not re.search(r"(?i)code|otp|pin|verif|κωδικ|passcode|password", text or ""):
        return None
    match = CODE.search(text or "")
    return match.group(1) if match else None


class Plugin(PacketStatePlugin):
    plugin_id = "notifications"
    display_name = _("Notifications")
    description = _("Show the paired device's notifications on this computer.")
    icon = "preferences-system-notifications-symbolic"
    incoming_capabilities = ("kdeconnect.notification",)
    outgoing_capabilities = (
        "kdeconnect.notification.request",
        "kdeconnect.notification.action",
        "kdeconnect.notification.reply",
    )
    packet_types = incoming_capabilities
    settings_schema = (
        {"key": "show", "label": _("Show notifications from this device"), "type": "bool", "default": True},
        {"key": "show_existing", "label": _("Also show the ones already on the device when it connects (quietly)"),
         "type": "bool", "default": True},
        {"key": "show_content", "label": _("Show notification text (turn off for privacy)"), "type": "bool", "default": True},
        {"key": "show_icons", "label": _("Show app icons and pictures"), "type": "bool", "default": True},
        {"key": "reply", "label": _("Offer Reply on notifications that allow it"), "type": "bool", "default": True},
        {"key": "sync_dismiss", "label": _("Dismissing a notification here dismisses it on the device"),
         "type": "bool", "default": True},
        {"key": "copy_codes", "label": _("Offer to copy verification codes"), "type": "bool", "default": True},
    )

    def __init__(self, downloader=None):
        self._shown = {}  # (device id, notification id) -> desktop notification id
        self._lock = threading.Lock()
        self._downloader = downloader  # tests replace the payload download

    def on_connected(self, daemon, device_id):
        daemon.send_plugin_packet(
            self.plugin_id, device_id, "kdeconnect.notification.request", {"request": True}
        )

    def handle_packet(self, daemon, link, packet):
        if packet.get("type") != "kdeconnect.notification":
            return False
        body = packet.get("body", {})
        key = (link["id"], str(body.get("id", "")))
        settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
        if body.get("isCancel"):
            # Dismissed on the device: take it off this desktop too.
            with self._lock:
                desktop_id = self._shown.pop(key, None)
            if desktop_id and settings.get("sync_dismiss", True):
                daemon.close_notification(desktop_id)
            return True
        if not settings.get("show", True) or (body.get("silent") and not settings.get("show_existing", True)):
            return True

        icon_hash = str(body.get("payloadHash") or "")
        if settings.get("show_icons", True) and HASH.match(icon_hash) and packet.get("payloadSize"):
            path = os.path.join(icon_dir(), icon_hash + ".png")
            if not os.path.exists(path):
                threading.Thread(target=self._fetch_icon_then_show,
                                 args=(daemon, link, packet, path, key, settings), daemon=True).start()
                return True
            self._show(daemon, link, body, key, settings, path)
        else:
            self._show(daemon, link, body, key, settings, None)
        return True

    def _fetch_icon_then_show(self, daemon, link, packet, path, key, settings):
        download = self._downloader
        if download is None:
            from satellite.plugins.share import receive_file

            def download(daemon, link, packet, folder, name):
                return receive_file(daemon, link, packet, folder, quiet=True, name=name)
        os.makedirs(icon_dir(), mode=0o700, exist_ok=True)
        fetched = download(daemon, link, packet, icon_dir(), os.path.basename(path))
        self._show(daemon, link, packet.get("body", {}), key, settings, fetched)

    def run_action(self, daemon, device_id, notification_id, action):
        daemon.send_plugin_packet(self.plugin_id, device_id, "kdeconnect.notification.action",
                                  {"key": notification_id, "action": action})

    def reply(self, daemon, link, reply_id, app, title, group):
        heading = _("Reply in {}").format(group) if group else _("Reply to {}").format(title or app)
        return daemon.prompt_text(
            heading, _("Your reply goes out from {} on {}.").format(app, link["name"]),
            lambda message: daemon.send_plugin_packet(
                self.plugin_id, link["id"], "kdeconnect.notification.reply",
                {"requestReplyId": reply_id, "message": message}))

    # -- desktop notifications --------------------------------------------------

    def _show(self, daemon, link, body, key, settings, icon_path):
        app, title, text, group = describe(body)
        private = not settings.get("show_content", True)
        if private:
            summary, text = app, _("New notification on {}").format(link["name"])
        else:
            summary = "{}: {}".format(app, title) if title else app

        device_id, notification_id = link["id"], key[1]
        actions = []
        for index, name in enumerate(body.get("actions") or ()):
            if isinstance(name, str) and name:
                actions.append(("a{}".format(index), name,
                                lambda name=name: self.run_action(daemon, device_id, notification_id, name)))
        reply_id = body.get("requestReplyId")
        if reply_id and settings.get("reply", True):
            actions.append(("reply", _("Reply in {}").format(group) if group else _("Reply"),
                            lambda: self.reply(daemon, link, reply_id, app, title, group)))
        code = None if private else verification_code("{} {}".format(title, text))
        if code and settings.get("copy_codes", True):
            actions.append(("copy-code", _("Copy {}").format(code), lambda: daemon.set_clipboard(code)))

        def closed(reason):
            with self._lock:
                if self._shown.get(key) == desktop_id[0]:
                    del self._shown[key]
            # 2: the user dismissed it here. Clear it on the device as well.
            if reason == 2 and body.get("isClearable", True) and settings.get("sync_dismiss", True):
                daemon.send_plugin_packet(self.plugin_id, device_id, "kdeconnect.notification.request",
                                          {"cancel": notification_id})

        with self._lock:
            replaces = self._shown.get(key, 0)
        # Already on the phone when it connected: there, but quiet and low-key.
        quiet = {"urgency": 0, "sound": False} if body.get("silent") else {}
        # A list, so closed() can read the id that notify() returns below.
        desktop_id = [daemon.notify(
            summary, text, icon=icon_path or "preferences-system-notifications-symbolic", actions=actions,
            replaces=replaces, timeout=8000, on_closed=closed, **quiet,
        )]
        if desktop_id[0]:
            with self._lock:
                if len(self._shown) > 500:
                    self._shown.clear()
                self._shown[key] = desktop_id[0]

