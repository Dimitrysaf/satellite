"""Application preferences."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


class PreferencesDialog(Gtk.Dialog):
    def __init__(self, parent, settings):
        super().__init__(title="Preferences", transient_for=parent, modal=True)
        self.set_default_size(420, -1)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self._settings = settings

        grid = Gtk.Grid(column_spacing=16, row_spacing=14)
        grid.set_border_width(16)
        self.get_content_area().add(grid)

        self._row(grid, 0, "Keep running in the background",
                  "Closing the window leaves Satellite in the system tray, so clipboard, "
                  "notifications and file transfers keep working.",
                  "background")
        self._row(grid, 1, "Start hidden",
                  "Do not show the window when Satellite starts (needs the option above).",
                  "start_hidden")
        self.show_all()

    def _row(self, grid, index, title, subtitle, key):
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        labels.set_hexpand(True)
        labels.pack_start(Gtk.Label(label=title, xalign=0), False, False, 0)
        hint = Gtk.Label(label=subtitle, xalign=0, wrap=True)
        hint.set_max_width_chars(40)
        hint.get_style_context().add_class("dim-label")
        labels.pack_start(hint, False, False, 0)
        switch = Gtk.Switch()
        switch.set_active(self._settings.get(key))
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active", lambda s, _p: self._settings.set(key, s.get_active()))
        grid.attach(labels, 0, index, 1, 1)
        grid.attach(switch, 1, index, 1, 1)
