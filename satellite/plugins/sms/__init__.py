"""SMS and MMS: read and send the paired phone's text messages.

The phone stays the source of truth. Satellite keeps the messages it has been
sent in memory only (never on disk) and hands them to the Satellite Messages
app over D-Bus. RCS chats are not reachable: Android keeps them out of the
SMS/MMS database that KDE Connect reads.
"""

import base64
import mimetypes
import os
import threading

from satellite.plugins._base import PluginAction
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

REQUEST_VERSION = 2
MESSAGE_TYPES = {1: "received", 2: "sent", 3: "draft", 4: "sending", 5: "failed", 6: "sending"}
MAX_ATTACHMENT = 10 * 1024 * 1024  # what carriers accept for MMS is far less


def attachment_dir(device_id):
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(cache, "satellite", "sms", device_id)


def clean_message(message):
    """Keep only well-formed fields of one message from the phone."""
    if not isinstance(message, dict):
        return None
    try:
        thread = int(message.get("thread_id"))
        uid = int(message.get("_id", 0))
        date = int(message.get("date", 0))
        kind = int(message.get("type", 0))
    except (TypeError, ValueError):
        return None
    addresses = [
        str(item.get("address")) for item in message.get("addresses") or ()
        if isinstance(item, dict) and item.get("address")
    ]
    attachments = []
    for item in message.get("attachments") or ():
        if isinstance(item, dict) and "part_id" in item:
            attachments.append({
                "part_id": int(item["part_id"]),
                "mime_type": str(item.get("mime_type") or ""),
                "thumbnail": str(item.get("encoded_thumbnail") or ""),
                "unique_identifier": os.path.basename(str(item.get("unique_identifier") or "")),
            })
    return {
        "id": uid, "thread_id": thread, "date": date, "type": kind,
        "direction": MESSAGE_TYPES.get(kind, "received"),
        "body": str(message.get("body") or ""),
        "addresses": addresses,
        "read": bool(message.get("read")) and str(message.get("read")) != "0",
        "sub_id": message.get("sub_id"),
        "attachments": attachments,
    }


def file_attachment(path):
    """An AttachmentContainer for a local file, or None if unusable."""
    try:
        if os.path.getsize(path) > MAX_ATTACHMENT:
            return None
        with open(path, "rb") as stream:
            data = stream.read()
    except OSError:
        return None
    return {
        "fileName": os.path.basename(path),
        "base64EncodedFile": base64.b64encode(data).decode("ascii"),
        "mimeType": mimetypes.guess_type(path)[0] or "application/octet-stream",
    }


class Plugin:
    plugin_id = "sms"
    display_name = _("Messages")
    description = _("Read and send the paired phone's text messages (SMS and MMS).")
    icon = "chat-message-new-symbolic"
    incoming_capabilities = ("kdeconnect.sms.messages", "kdeconnect.sms.attachment_file")
    outgoing_capabilities = (
        "kdeconnect.sms.request",
        "kdeconnect.sms.request_conversations",
        "kdeconnect.sms.request_conversation",
        "kdeconnect.sms.request_attachment",
    )

    def __init__(self):
        self._messages = {}  # device id -> {(thread, id) -> message}
        self._lock = threading.Lock()

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.sms.messages" in remote_outgoing

    @staticmethod
    def configure(ctx, _settings):
        """Google Messages for web and signing out of it (GTK, loaded only when asked)."""
        from satellite_sms import settings_dialog
        settings_dialog.run(ctx.parent)
        return None

    # -- incoming ----------------------------------------------------------

    def handle_packet(self, daemon, link, packet):
        packet_type = packet.get("type")
        if packet_type == "kdeconnect.sms.messages":
            body = packet.get("body", {})
            messages = [m for m in map(clean_message, body.get("messages") or ()) if m]
            with self._lock:
                store = self._messages.setdefault(link["id"], {})
                for message in messages:
                    store[(message["thread_id"], message["id"])] = message
            daemon.emit_event("sms.messages", link["id"], messages)
            return True
        if packet_type == "kdeconnect.sms.attachment_file" and packet.get("payloadSize") is not None:
            threading.Thread(target=self._receive_attachment, args=(daemon, link, packet), daemon=True).start()
            return True
        return False

    def _receive_attachment(self, daemon, link, packet):
        from satellite.plugins.share import receive_file
        name = os.path.basename(str(packet.get("body", {}).get("filename") or ""))
        path = receive_file(daemon, link, packet, attachment_dir(link["id"]), quiet=True)
        if path:
            daemon.emit_event("sms.attachment", link["id"], {"unique_identifier": name, "path": path})

    # -- requests from the Messages app -------------------------------------

    def conversations(self, device_id):
        """The newest cached message of each thread, newest thread first."""
        with self._lock:
            latest = {}
            for message in self._messages.get(device_id, {}).values():
                current = latest.get(message["thread_id"])
                if current is None or message["date"] > current["date"]:
                    latest[message["thread_id"]] = message
        return sorted(latest.values(), key=lambda m: m["date"], reverse=True)

    def thread(self, device_id, thread_id):
        with self._lock:
            messages = [m for (t, _i), m in self._messages.get(device_id, {}).items() if t == thread_id]
        return sorted(messages, key=lambda m: (m["date"], m["id"]))

    @staticmethod
    def request_conversations(daemon, device_id):
        return daemon.send_plugin_packet("sms", device_id, "kdeconnect.sms.request_conversations", {})

    @staticmethod
    def request_conversation(daemon, device_id, thread_id, start=0, count=0):
        body = {"threadID": int(thread_id)}
        if start:
            body["rangeStartTimestamp"] = int(start)
        if count:
            body["numberToRequest"] = int(count)
        return daemon.send_plugin_packet("sms", device_id, "kdeconnect.sms.request_conversation", body)

    @staticmethod
    def request_attachment(daemon, device_id, part_id, unique_identifier):
        cached = os.path.join(attachment_dir(device_id), os.path.basename(unique_identifier))
        if unique_identifier and os.path.isfile(cached):
            daemon.emit_event("sms.attachment", device_id,
                              {"unique_identifier": unique_identifier, "path": cached})
            return True
        return daemon.send_plugin_packet("sms", device_id, "kdeconnect.sms.request_attachment", {
            "part_id": int(part_id), "unique_identifier": unique_identifier})

    @staticmethod
    def send_message(daemon, device_id, addresses, text, files=(), sub_id=None):
        """Send an SMS, or an MMS when there are attachments or several recipients."""
        addresses = [a.strip() for a in addresses if a and a.strip()]
        if not addresses or not (text or files):
            return False
        body = {"version": REQUEST_VERSION, "addresses": [{"address": a} for a in addresses]}
        if text:
            body["messageBody"] = text
        attachments = [a for a in map(file_attachment, files) if a]
        if len(attachments) != len(files):
            return False
        if attachments:
            body["attachments"] = attachments
        if sub_id is not None:
            body["subID"] = sub_id
        return daemon.send_plugin_packet("sms", device_id, "kdeconnect.sms.request", body)

    @staticmethod
    def send(daemon, device_id, packet_type=None, body=None, **_kwargs):
        if packet_type not in Plugin.outgoing_capabilities:
            return False
        return daemon._send_plugin_packet(device_id, packet_type, body or {})

    def actions(self, device):
        def run(ctx):
            from satellite.launch import launch
            if not launch("satellite-sms", "satellite_sms", "--device", ctx.device_id):
                ctx.error(_("Could not open Messages"), _("Satellite Messages is not installed."))

        return [PluginAction("messages", _("Messages"), "chat-message-new-symbolic", run,
                             _("Read and send text messages"))]
