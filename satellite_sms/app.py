"""Satellite Messages application (satellite-sms)."""

import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from satellite.phone_client import PhoneClient
from satellite_sms import APP_ID
from satellite_sms.settings_dialog import SIGN_OUT_ACTION
from satellite_sms.window import MessagesWindow
from satellite.i18n import _


class MessagesApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.add_main_option("device", ord("d"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             _("Show this phone's messages"), "ID")
        self.add_main_option("to", ord("t"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             _("Start a message to this number"), "NUMBER")
        # Activated over D-Bus by the Messages plugin settings in Satellite.
        sign_out = Gio.SimpleAction.new(SIGN_OUT_ACTION, None)
        sign_out.connect("activate", lambda *_a: self.window and self.window.sign_out_google())
        self.add_action(sign_out)

    def do_command_line(self, command_line):
        options = command_line.get_options_dict().end().unpack()
        if self.window is None:
            GLib.set_application_name(_("Messages"))
            self.window = MessagesWindow(self, PhoneClient())
        self.window.present()
        self.window.start(options.get("device"), options.get("to"))
        return 0


def main():
    return MessagesApplication().run(sys.argv)
