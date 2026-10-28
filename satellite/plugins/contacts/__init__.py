"""Keep a copy of the paired phone's contacts as vCard files.

Like KDE Connect: the phone lists every contact's UID with a timestamp,
Satellite asks for the vCards that are new or changed and deletes the ones
the phone no longer has. The files live in
``~/.local/share/satellite/contacts/<device id>/<uid>.vcf`` and are read by
Satellite Contacts and Satellite Messages. They are also shared with the
desktop's address books and, optionally, KDE apps (see contacts_share).
"""

import os
import re
import threading

from satellite.plugins._base import PluginAction
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

TIMESTAMP = re.compile(r"^X-KDECONNECT-TIMESTAMP:(-?\d+)\s*$", re.MULTILINE)


def contacts_root():
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(data, "satellite", "contacts")


def contacts_dir(device_id):
    return os.path.join(contacts_root(), os.path.basename(device_id))


def _vcard_path(directory, uid):
    """The file for a UID, or None if the UID would escape the folder."""
    path = os.path.normpath(os.path.join(directory, "{}.vcf".format(uid)))
    return path if os.path.dirname(path) == os.path.normpath(directory) else None


def _local_timestamp(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            match = TIMESTAMP.search(stream.read())
    except OSError:
        return None
    return int(match.group(1)) if match else None


class Plugin:
    plugin_id = "contacts"
    display_name = _("Contacts")
    description = _("Keep a copy of the paired phone's contacts on this computer.")
    icon = "x-office-address-book-symbolic"
    incoming_capabilities = (
        "kdeconnect.contacts.response_uids_timestamps",
        "kdeconnect.contacts.response_vcards",
    )
    outgoing_capabilities = (
        "kdeconnect.contacts.request_all_uids_timestamps",
        "kdeconnect.contacts.request_vcards_by_uid",
    )
    settings_schema = (
        {"key": "receive", "label": _("Copy the phone's contacts to this computer"),
         "type": "bool", "default": True},
        {"key": "share_desktop", "label": _("Share them with other address book apps "
         "(GNOME Contacts, Evolution, ...)"), "type": "bool", "default": True},
        {"key": "share_kde", "label": _("Also share them with KDE apps"), "type": "bool", "default": False},
    )

    def __init__(self, sharer=None):
        self._sharer = sharer  # tests replace the contacts_share module
        self._share_lock = threading.Lock()
        self.share_thread = None  # the latest background share (tests wait on it)

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.contacts.response_uids_timestamps" in remote_outgoing

    def on_connected(self, daemon, device_id):
        if daemon.plugins.get_settings(device_id, self.plugin_id).get("receive", True):
            self.sync(daemon, device_id)
            # Also applies sharing settings changed while the phone was away.
            self._share(daemon, device_id)

    @staticmethod
    def sync(daemon, device_id):
        return daemon.send_plugin_packet(
            "contacts", device_id, "kdeconnect.contacts.request_all_uids_timestamps", {})

    def handle_packet(self, daemon, link, packet):
        packet_type = packet.get("type")
        if packet_type not in self.incoming_capabilities:
            return False
        if not daemon.plugins.get_settings(link["id"], self.plugin_id).get("receive", True):
            return True
        body = packet.get("body", {})
        uids = [str(uid) for uid in body.get("uids") or () if isinstance(uid, (str, int))]
        directory = contacts_dir(link["id"])
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if packet_type == "kdeconnect.contacts.response_uids_timestamps":
            self._compare(daemon, link["id"], directory, uids, body)
        else:
            self._store(daemon, link["id"], directory, uids, body)
        return True

    def _compare(self, daemon, device_id, directory, uids, body):
        wanted, keep = [], set()
        for uid in uids:
            path = _vcard_path(directory, uid)
            if path is None:
                continue
            keep.add(os.path.basename(path))
            try:
                remote = int(body.get(uid))
            except (TypeError, ValueError):
                remote = None
            if remote is None or not os.path.exists(path) or _local_timestamp(path) != remote:
                wanted.append(uid)
        removed = False
        for name in os.listdir(directory):
            if name.endswith((".vcf", ".vcard")) and name not in keep:
                os.remove(os.path.join(directory, name))
                removed = True
        if wanted:
            daemon.send_plugin_packet(
                self.plugin_id, device_id, "kdeconnect.contacts.request_vcards_by_uid", {"uids": wanted})
        elif removed:
            daemon.emit_event("contacts.changed", device_id)
            self._share(daemon, device_id)

    def _store(self, daemon, device_id, directory, uids, body):
        for uid in uids:
            path, card = _vcard_path(directory, uid), body.get(uid)
            if path is None or not isinstance(card, str):
                continue
            temporary = path + ".tmp"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(card)
            os.replace(temporary, path)
        daemon.emit_event("contacts.changed", device_id)
        self._share(daemon, device_id)

    # -- sharing with other address book apps ------------------------------------

    def _module(self):
        if self._sharer is None:
            from satellite import contacts_share
            self._sharer = contacts_share
        return self._sharer

    def _share(self, daemon, device_id):
        """Update the shared copies in the background (address books are slow)."""
        settings = daemon.plugins.get_settings(device_id, self.plugin_id)
        name = next((d["name"] for d in daemon.devices() if d["id"] == device_id), "Phone")

        def run():
            with self._share_lock:
                try:
                    module = self._module()
                    module.share(device_id, name, contacts_dir(device_id),
                                 to_eds=settings.get("share_desktop", True),
                                 to_kpeople=settings.get("share_kde", False))
                    module.unshare(device_id, eds=not settings.get("share_desktop", True),
                                   kpeople=not settings.get("share_kde", False))
                except Exception as error:  # sharing must never break the sync
                    print("Could not share contacts: {!r}".format(error), flush=True)

        self.share_thread = threading.Thread(target=run, daemon=True)
        self.share_thread.start()

    def settings_changed(self, daemon, device_id):
        self._share(daemon, device_id)

    @staticmethod
    def send(daemon, device_id, packet_type=None, body=None, **_kwargs):
        if packet_type not in Plugin.outgoing_capabilities:
            return False
        return daemon._send_plugin_packet(device_id, packet_type, body or {})

    def actions(self, device):
        def run(ctx):
            from satellite.launch import launch
            if not launch("satellite-contacts", "satellite_contacts", "--device", ctx.device_id):
                ctx.error(_("Could not open Contacts"), _("Satellite Contacts is not installed."))

        return [PluginAction("contacts", _("Contacts"), "x-office-address-book-symbolic", run,
                             _("See the phone's contacts"))]
