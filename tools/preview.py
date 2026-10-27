#!/usr/bin/python3
"""Open Satellite's real window on mock devices, optionally saving screenshots.

    python3 tools/preview.py                  # interactive
    xvfb-run -a python3 tools/preview.py --shots /tmp/shots   # headless PNGs
"""

import argparse
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk


def make_window(app):
    """Build a SatelliteWindow whose daemon only knows the mock devices."""
    import mock_devices
    from daemon.daemon import SatelliteDaemon
    from satellite.notifier import Notifier
    from satellite.settings import AppSettings
    from satellite.ui.main_window import SatelliteWindow

    daemon = SatelliteDaemon()
    daemon._devices = {d["id"]: d for d in mock_devices.build(daemon.plugins)}
    daemon.start = daemon.stop = daemon.refresh = lambda: None
    window = SatelliteWindow(app, daemon, AppSettings(daemon.config_dir), Notifier())
    return window, daemon


def capture(window, path):
    gdk_window = window.get_window()
    pixbuf = Gdk.pixbuf_get_from_window(
        gdk_window, 0, 0, gdk_window.get_width(), gdk_window.get_height())
    pixbuf.savev(path, "png", [], [])
    print("saved", path, flush=True)


def run_shots(app, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    window, daemon = make_window(app)
    ids = {d["name"]: d["id"] for d in daemon.devices()}
    steps = []

    def at(delay, func):
        steps.append((delay, func))

    def select(name):
        window.sidebar.select(ids[name])

    def shot(name):
        return lambda: capture(window, os.path.join(out_dir, name))

    def dialog_shot(name, opener, size=None):
        def run():
            GLib.timeout_add(600, finish)
            opener()

        def finish():
            dialogs = [w for w in Gtk.Window.list_toplevels()
                       if isinstance(w, Gtk.Dialog) and w.get_visible()]
            if dialogs:
                capture(dialogs[0], os.path.join(out_dir, name))
                dialogs[0].response(Gtk.ResponseType.CANCEL)
            return False
        return run

    window.resize(900, 600)
    at(700, shot("01-no-selection.png"))
    at(300, lambda: select("Pixel 7"))
    at(700, shot("02-connected-device.png"))
    at(300, lambda: select("Moto G Power"))
    at(500, shot("03-pairing-request.png"))
    at(300, lambda: select("Surface Pro 9"))
    at(500, shot("04-detected-device.png"))
    at(300, lambda: select("Galaxy Tab S8"))
    at(500, shot("05-offline-device.png"))
    at(300, lambda: select("Pixel 7"))
    at(500, dialog_shot("06-plugin-settings.png",
                        lambda: window.configure_plugin(ids["Pixel 7"], "ping")))
    at(1500, dialog_shot("07-runcommand-dialog.png",
                         lambda: window.configure_plugin(ids["Pixel 7"], "runcommand")))
    at(1500, lambda: window.resize(430, 640))
    at(600, window.go_back)
    at(600, shot("08-narrow-list.png"))
    at(300, lambda: select("Pixel 7"))
    at(900, shot("09-narrow-device.png"))
    at(300, lambda: sys.exit(0) or app.quit())

    def next_step(index=0):
        if index >= len(steps):
            return False
        delay, func = steps[index]
        GLib.timeout_add(delay, lambda: (func(), next_step(index + 1), False)[2])
        return False

    window.present()
    next_step()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", help="write screenshots to this folder and exit")
    args = parser.parse_args()
    os.environ["HOME"] = tempfile.mkdtemp(prefix="satellite-preview-")

    from gi.repository import Gio

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="org.x.Satellite.Preview", flags=Gio.ApplicationFlags.FLAGS_NONE)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            for name in ("preferences", "about", "quit"):
                action = Gio.SimpleAction.new(name, None)
                self.add_action(action)

        def do_activate(self):
            if args.shots:
                run_shots(self, args.shots)
            else:
                window, _daemon = make_window(self)
                window.present()

    return App().run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
