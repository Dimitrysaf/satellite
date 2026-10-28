"""What a plugin callback may do with the window, without knowing GTK."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from satellite.ui.dialogs import ask_text, show_message
from satellite.i18n import _


class ActionContext:
    """Passed to ``PluginAction.callback`` and ``Plugin.configure``."""

    def __init__(self, window, daemon, device_id):
        self.parent = window
        self.daemon = daemon
        self.device_id = device_id

    @property
    def device(self):
        for device in self.daemon.devices():
            if device["id"] == self.device_id:
                return device
        return {"id": self.device_id, "name": _("this device")}

    def error(self, title, text):
        show_message(self.parent, title, text, Gtk.MessageType.WARNING)

    def info(self, title, text):
        show_message(self.parent, title, text, Gtk.MessageType.INFO)

    def ask_text(self, title, label, initial="", accept_label="OK"):
        return ask_text(self.parent, title, label, initial, accept_label)

    def clipboard_text(self):
        return Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text()

    def choose_files(self, title):
        chooser = Gtk.FileChooserDialog(
            title=title, transient_for=self.parent,
            action=Gtk.FileChooserAction.OPEN,
        )
        chooser.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        chooser.add_button(_("Send"), Gtk.ResponseType.ACCEPT)
        chooser.set_select_multiple(True)
        paths = chooser.get_filenames() if chooser.run() == Gtk.ResponseType.ACCEPT else []
        chooser.destroy()
        return paths


class BackgroundContext(ActionContext):
    """For actions run from the tray without opening the window."""

    def error(self, title, text):
        self.daemon.notify(title, text, icon="dialog-warning-symbolic")

    def info(self, title, text):
        self.daemon.notify(title, text, icon="dialog-information-symbolic")
