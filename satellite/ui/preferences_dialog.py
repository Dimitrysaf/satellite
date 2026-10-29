"""Application preferences."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from satellite import autostart
from satellite.i18n import _


class PreferencesDialog(Gtk.Dialog):
    def __init__(self, parent, settings, daemon=None):
        super().__init__(title=_("Preferences"), transient_for=parent, modal=True)
        self.set_default_size(420, -1)
        self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        self._settings = settings

        grid = Gtk.Grid(column_spacing=16, row_spacing=14)
        grid.set_border_width(16)
        self.get_content_area().add(grid)

        self._row(grid, 0, _("Start when you log in"),
                  _("Satellite starts minimized to the system tray."),
                  autostart.is_enabled(), autostart.set_enabled)
        self._row(grid, 1, _("Keep running when the window is closed"),
                  _("Satellite stays in the system tray, so clipboard, notifications and "
                  "file transfers keep working. Quit from the tray menu."),
                  settings.get("background"), lambda value: settings.set("background", value))
        if daemon is not None:
            self._discovery_rows(grid, 2, daemon)
        self.show_all()

    def _discovery_rows(self, grid, first, daemon):
        discovery = daemon.discovery

        def change(key):
            def apply(value):
                discovery.set(key, value)
                daemon.apply_discovery_settings()
            return apply

        heading = Gtk.Label(xalign=0, margin_top=8)
        heading.set_markup("<b>{}</b>".format(GLib.markup_escape_text(_("Finding devices"))))
        grid.attach(heading, 0, first, 2, 1)
        self._row(grid, first + 1, _("Announce on the local network"),
                  _("The classic KDE Connect broadcast."),
                  discovery.get("broadcast"), change("broadcast"))
        self._row(grid, first + 2, _("Use mDNS (Avahi)"),
                  _("Finds devices on networks that block broadcasts, like some routers and guest Wi-Fi."),
                  discovery.get("mdns"), change("mdns"))

    def _row(self, grid, index, title, subtitle, active, on_change):
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        labels.set_hexpand(True)
        labels.pack_start(Gtk.Label(label=title, xalign=0), False, False, 0)
        hint = Gtk.Label(label=subtitle, xalign=0, wrap=True)
        hint.set_max_width_chars(40)
        hint.get_style_context().add_class("dim-label")
        labels.pack_start(hint, False, False, 0)
        switch = Gtk.Switch()
        switch.set_active(active)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active", lambda s, _p: on_change(s.get_active()))
        grid.attach(labels, 0, index, 1, 1)
        grid.attach(switch, 1, index, 1, 1)
