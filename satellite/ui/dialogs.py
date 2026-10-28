"""Plain message, confirmation and text-entry dialogs."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
from satellite.i18n import _


def show_message(parent, title, text, kind=Gtk.MessageType.WARNING):
    dialog = Gtk.MessageDialog(
        transient_for=parent, modal=True, message_type=kind,
        buttons=Gtk.ButtonsType.OK, text=title,
    )
    dialog.format_secondary_text(text)
    dialog.run()
    dialog.destroy()


def confirm(parent, title, text, accept_label):
    dialog = Gtk.MessageDialog(
        transient_for=parent, modal=True, message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.NONE, text=title,
    )
    dialog.format_secondary_text(text)
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    dialog.add_button(accept_label, Gtk.ResponseType.ACCEPT)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.ACCEPT


def ask_text(parent, title, label, initial="", accept_label=_("Save")):
    """Ask for one line of text. Returns the text, or None if cancelled."""
    dialog = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dialog.set_keep_above(parent is None)  # e.g. a reply from a notification
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    dialog.add_button(accept_label, Gtk.ResponseType.ACCEPT)
    dialog.set_default_response(Gtk.ResponseType.ACCEPT)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_border_width(12)
    box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
    entry = Gtk.Entry()
    entry.set_text(initial)
    entry.set_width_chars(32)
    entry.set_activates_default(True)
    box.pack_start(entry, False, False, 0)
    dialog.get_content_area().add(box)
    dialog.show_all()
    response = dialog.run()
    text = entry.get_text()
    dialog.destroy()
    return text if response == Gtk.ResponseType.ACCEPT else None


def show_encryption_info(parent, device):
    encryption = device.get("encryption", {})
    show_message(
        parent, _("Encrypted connection"),
        _("Device: {}\nProtocol: {}\nCipher: {}\nCertificate SHA-256: {}").format(
            device["name"],
            encryption.get("protocol") or _("Unknown TLS version"),
            encryption.get("cipher") or _("Unknown cipher suite"),
            encryption.get("fingerprint") or _("Unavailable"),
        ),
        Gtk.MessageType.INFO,
    )
