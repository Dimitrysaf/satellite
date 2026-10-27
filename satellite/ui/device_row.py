"""One row in the device sidebar."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from satellite.ui.widgets import icon_for, short_status


class DeviceRow(Gtk.ListBoxRow):
    def __init__(self, device):
        super().__init__()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        for side in ("top", "bottom"):
            getattr(box, "set_margin_" + side)(6)
        box.set_margin_start(10)
        box.set_margin_end(10)

        self.icon = Gtk.Image.new_from_icon_name(icon_for(device["type"]), Gtk.IconSize.LARGE_TOOLBAR)
        box.pack_start(self.icon, False, False, 0)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.name_label = Gtk.Label(xalign=0)
        labels.pack_start(self.name_label, False, False, 0)
        self.status_label = Gtk.Label(xalign=0)
        self.status_label.get_style_context().add_class("dim-label")
        labels.pack_start(self.status_label, False, False, 0)
        box.pack_start(labels, True, True, 0)

        self.add(box)
        self.update(device)

    def update(self, device):
        self.device = device
        self.icon.set_from_icon_name(icon_for(device["type"]), Gtk.IconSize.LARGE_TOOLBAR)
        self.name_label.set_text(device["name"])
        self.status_label.set_text(short_status(device))
