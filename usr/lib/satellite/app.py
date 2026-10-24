import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk

from satellite.common import APP_ID


class SatelliteWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Satellite")
        self.set_default_size(480, 360)


class SatelliteApplication(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.window = None

    def do_activate(self):
        if self.window is None:
            self.window = SatelliteWindow(self)
        self.window.present()


def main():
    app = SatelliteApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
