#!/usr/bin/python3
"""Satellite: a KDE Connect client for the Cinnamon desktop."""

import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("XApp", "1.0")
from gi.repository import Gio, Gtk

from daemon.daemon import SatelliteDaemon
from satellite import APP_ID
from satellite.notifier import Notifier
from satellite.settings import AppSettings
from satellite.ui.main_window import SatelliteWindow
from satellite.ui.preferences_dialog import PreferencesDialog
from satellite.ui.tray import Tray
from satellite.ui.widgets import load_satellite_logo

PROJECT_VERSION = "__PROJECT_VERSION__"


class SatelliteApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None
        self.tray = None

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

    def do_activate(self):
        if self.window is None:
            daemon = SatelliteDaemon()
            settings = AppSettings(daemon.config_dir)
            self.tray = Tray(self.toggle_window, self.quit)
            self.window = SatelliteWindow(
                self, daemon, settings, Notifier(), tray_available=True)
            self.tray.set_visible(bool(settings.get("background")))
            if settings.get("background") and settings.get("start_hidden"):
                return
        self.window.present()

    def toggle_window(self, force_show=False):
        if self.window is None:
            return
        if self.window.get_visible() and not force_show:
            self.window.hide()
        else:
            self.window.present()

    def on_preferences(self, _action, _param):
        dialog = PreferencesDialog(self.window, self.window.settings)
        dialog.run()
        dialog.destroy()
        self.tray.set_visible(bool(self.window.settings.get("background")))

    def on_about(self, _action, _param):
        about = Gtk.AboutDialog(transient_for=self.window, modal=True)
        about.set_program_name("Satellite")
        about.set_version(PROJECT_VERSION)
        about.set_comments("Connect your phone and computer with the KDE Connect protocol.")
        about.set_website("https://github.com/Dimitrysaf/satellite")
        logo = load_satellite_logo()
        if logo is not None:
            about.set_logo(logo)
        else:
            about.set_logo_icon_name("satellite")
        about.set_license_type(Gtk.License.GPL_3_0)
        about.connect("response", lambda dialog, _response: dialog.destroy())
        about.present()


def main():
    return SatelliteApplication().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
