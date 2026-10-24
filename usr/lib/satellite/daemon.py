#!/usr/bin/python3
import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from common import DBUS_NAME


class SatelliteDaemon:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.owner_id = None

    def run(self):
        self.owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            DBUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            None,
            None,
        )
        self.loop.run()

    def _on_bus_acquired(self, connection, name):
        pass

    def stop(self):
        if self.owner_id is not None:
            Gio.bus_unown_name(self.owner_id)
        self.loop.quit()


def main():
    SatelliteDaemon().run()


if __name__ == "__main__":
    main()
