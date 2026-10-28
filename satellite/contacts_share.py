"""Share the phone's contacts with other address book apps.

* Evolution Data Server: one address book per phone, "<phone> (Satellite)".
  GNOME Contacts, Evolution, GNOME Calendar (birthdays) and other apps that
  use the desktop's shared address books see it.
* KDE apps (KPeople) read vCards from ``~/.local/share/kpeoplevcard``.

Both get clean vCard 3.0 copies of Satellite's synced files. The phone stays
the source: the copies are made to match the phone on every sync, so changes
made in other apps are replaced.
"""

import os
import shutil

from satellite.vcard import load_directory, to_vcard30

SOURCE_PREFIX = "satellite-"
# (guint32) -1: don't wait for a "connected" state, which local address books
# never report (0 would wait forever).
NO_WAIT = 0xFFFFFFFF


def kpeople_dir(device_id):
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(data, "kpeoplevcard", "satellite-" + os.path.basename(device_id))


def mirror_kpeople(device_id, contacts):
    """Write the vCards KDE apps read, and remove ones the phone dropped."""
    folder = kpeople_dir(device_id)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    wanted = {}
    for contact in contacts:
        wanted[contact.uid + ".vcf"] = to_vcard30(contact)
    for name in os.listdir(folder):
        if name.endswith(".vcf") and name not in wanted:
            os.remove(os.path.join(folder, name))
    for name, text in wanted.items():
        path = os.path.join(folder, name)
        try:
            with open(path, "r", encoding="utf-8") as stream:
                if stream.read() == text:
                    continue
        except OSError:
            pass
        with open(path + ".tmp", "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(path + ".tmp", path)


def remove_kpeople(device_id):
    shutil.rmtree(kpeople_dir(device_id), ignore_errors=True)


def _eds():
    """The Evolution Data Server modules, or None if not installed."""
    try:
        import gi
        gi.require_version("EDataServer", "1.2")
        gi.require_version("EBook", "1.2")
        gi.require_version("EBookContacts", "1.2")
        from gi.repository import EBook, EBookContacts, EDataServer
    except (ImportError, ValueError):
        return None
    return EDataServer, EBook, EBookContacts


def eds_available():
    return _eds() is not None


class EdsAddressBooks:
    """One local Evolution Data Server address book per phone."""

    def __init__(self):
        modules = _eds()
        if modules is None:
            raise RuntimeError("Evolution Data Server (gir1.2-ebook-1.2) is not installed")
        self.EDataServer, self.EBook, self.EBookContacts = modules
        self.registry = self.EDataServer.SourceRegistry.new_sync(None)

    def _source(self, device_id, name, create=True):
        uid = SOURCE_PREFIX + device_id
        source = self.registry.ref_source(uid)
        display = "{} (Satellite)".format(name)
        if source is None:
            if not create:
                return None
            source = self.EDataServer.Source.new_with_uid(uid, None)
            source.set_display_name(display)
            source.set_parent("local-stub")
            source.get_extension(self.EDataServer.SOURCE_EXTENSION_ADDRESS_BOOK).set_backend_name("local")
            self.registry.commit_source_sync(source, None)
        elif source.get_display_name() != display:
            source.set_display_name(display)
            source.write_sync(None)
        return source

    def sync(self, device_id, name, contacts):
        """Make the phone's address book match ``contacts``.

        Returns (added, updated, removed) counts.
        """
        source = self._source(device_id, name)
        client = self.EBook.BookClient.connect_sync(source, NO_WAIT, None)
        query = self.EBookContacts.BookQuery.any_field_contains("").to_string()
        _ok, existing = client.get_contacts_sync(query, None)
        stamps = {}
        for contact in existing:
            attribute = contact.get_attribute("X-KDECONNECT-TIMESTAMP")
            stamps[contact.get_property("id")] = attribute.get_value() if attribute else None

        flags = self.EBookContacts.BookOperationFlags.NONE
        new, changed = [], []
        for contact in contacts:
            card = self.EBookContacts.Contact.new_from_vcard_with_uid(to_vcard30(contact), contact.uid)
            if contact.uid not in stamps:
                new.append(card)
            elif stamps[contact.uid] != str(contact.timestamp) or contact.timestamp is None:
                changed.append(card)
        gone = [uid for uid in stamps if uid not in {c.uid for c in contacts}]
        if new:
            client.add_contacts_sync(new, flags, None)
        if changed:
            client.modify_contacts_sync(changed, flags, None)
        if gone:
            client.remove_contacts_sync(gone, flags, None)
        return len(new), len(changed), len(gone)

    def remove(self, device_id):
        """Delete the phone's address book (when sharing is switched off)."""
        source = self._source(device_id, "", create=False)
        if source is not None and source.get_removable():
            source.remove_sync(None)


def share(device_id, name, directory, to_eds=True, to_kpeople=False):
    """Bring every enabled shared copy in line with the synced files."""
    contacts = load_directory(directory, device_id)
    if to_eds and eds_available():
        EdsAddressBooks().sync(device_id, name, contacts)
    if to_kpeople:
        mirror_kpeople(device_id, contacts)


def unshare(device_id, eds=True, kpeople=True):
    if eds and eds_available():
        EdsAddressBooks().remove(device_id)
    if kpeople:
        remove_kpeople(device_id)


def export(contacts, path):
    """Save contacts as one .vcf file (Thunderbird and most apps import it)."""
    with open(path, "w", encoding="utf-8") as stream:
        for contact in contacts:
            stream.write(to_vcard30(contact))
