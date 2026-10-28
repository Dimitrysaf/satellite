"""Satellite Contacts application (satellite-contacts)."""

import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from satellite.phone_client import PhoneClient
from satellite_contacts import APP_ID
from satellite_contacts.window import ContactsWindow
from satellite.i18n import _


class ContactsApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.add_main_option("device", ord("d"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             _("Show this phone's contacts"), "ID")

    def do_command_line(self, command_line):
        options = command_line.get_options_dict().end().unpack()
        if self.window is None:
            GLib.set_application_name(_("Contacts"))
            self.window = ContactsWindow(self, PhoneClient())
        self.window.present()
        self.window.start(options.get("device"))
        return 0


def main():
    return ContactsApplication().run(sys.argv)
