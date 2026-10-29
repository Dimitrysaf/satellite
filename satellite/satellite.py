#!/usr/bin/python3
"""Satellite: a KDE Connect client for the Cinnamon desktop."""

import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("XApp", "1.0")
from gi.repository import Gio, GLib, Gtk

from daemon.daemon import SatelliteDaemon
from satellite import APP_ID
from satellite.notifier import Notifier
from satellite.phone_service import PhoneService
from satellite.settings import AppSettings
from satellite.ui.main_window import SatelliteWindow
from satellite.ui.preferences_dialog import PreferencesDialog
from satellite.ui.tray import Tray
from satellite.ui.widgets import load_satellite_logo
from satellite.i18n import _

PROJECT_VERSION = "__PROJECT_VERSION__"


class SatelliteApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.tray = None
        self.phone = PhoneService(self._window_for_service)
        self.add_main_option(
            "background", ord("b"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            _("Start minimized to the system tray"), None)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        for name, callback, accels in (
            ("preferences", self.on_preferences, ["<Primary>comma"]),
            ("about", self.on_about, []),
            ("quit", lambda *_: self.quit(), ["<Primary>q"]),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            self.set_accels_for_action("app." + name, accels)

    def do_dbus_register(self, connection, object_path):
        Gtk.Application.do_dbus_register(self, connection, object_path)
        self.phone.register(connection)  # for Satellite Messages and Contacts
        return True

    def do_dbus_unregister(self, connection, object_path):
        self.phone.unregister()
        Gtk.Application.do_dbus_unregister(self, connection, object_path)

    def _window_for_service(self):
        if self.window is None:
            self.start(hidden=True)
        return self.window

    def do_command_line(self, command_line):
        # Also runs in the first instance when Satellite is launched again.
        self.start(hidden=command_line.get_options_dict().contains("background"))
        return 0

    def do_activate(self):
        self.start(hidden=False)

    def start(self, hidden):
        if self.window is None:
            daemon = SatelliteDaemon()
            settings = AppSettings(daemon.config_dir)
            self.window = SatelliteWindow(
                self, daemon, settings, Notifier(), tray_available=True)
            self.tray = Tray(self.window, self.toggle_window, self.quit)
            self.phone.attach(self.window)
            if hidden:
                return  # the hidden window keeps the application running
        elif hidden:
            return  # e.g. Messages making sure Satellite runs: don't pop the window up
        self.window.present()

    def toggle_window(self, force_show=False):
        if self.window is None:
            return
        if self.window.get_visible() and not force_show:
            self.window.hide()
        else:
            self.window.present()

    def on_preferences(self, _action, _param):
        dialog = PreferencesDialog(self.window, self.window.settings, self.window.daemon)
        dialog.run()
        dialog.destroy()

    def on_about(self, _action, _param):
        about = Gtk.AboutDialog(transient_for=self.window, modal=True)
        about.set_program_name(_("Satellite"))
        about.set_version(PROJECT_VERSION)
        about.set_comments(_("Connect your phone and computer with the KDE Connect protocol."))
        about.set_website("https://github.com/Dimitrysaf/satellite")
        # By name, like the other XApps: GTK draws it at the About dialog's own size.
        if Gtk.IconTheme.get_default().has_icon("satellite"):
            about.set_logo_icon_name("satellite")
        else:  # running from the source tree, not installed
            logo = load_satellite_logo(128)
            if logo is not None:
                about.set_logo(logo)
        about.set_license_type(Gtk.License.GPL_3_0)
        about.connect("response", lambda dialog, _response: dialog.destroy())
        about.present()


def main():
    return SatelliteApplication().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
